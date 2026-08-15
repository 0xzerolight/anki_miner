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
│ 2. Filter Unknown Words                             │
│    WordFilterService + AnkiService                  │
│    + optional: MultiFrequencyService,               │
│      WordListService, KnownWordDB                   │
│    → list[TokenizedWord] (unknown only)             │
├─────────────────────────────────────────────────────┤
│ 3. Extract Media                                    │
│    MediaExtractorService (ffmpeg, parallel)          │
│    → list[(TokenizedWord, MediaData)]               │
├─────────────────────────────────────────────────────┤
│ 4. Fetch Definitions                                │
│    DefinitionService → DictionaryRegistry chain     │
│    (IndexedDictProvider offline dicts, first-hit-   │
│     wins; JishoProvider opt-in fallback)            │
│    → (definitions, glossaries, pitch_data)          │
├─────────────────────────────────────────────────────┤
│ 5. Create Anki Cards                                │
│    AnkiService (AnkiConnect HTTP API)               │
│    → cards_created count                            │
└─────────────────────────────────────────────────────┘
  │
  ▼
ProcessingResult
```

Before Phase 1, a pre-flight step validates the configured note type, field mapping, and target deck against Anki. Nothing is created — a missing deck is an error. Cancellation is checked between each phase. An optional curation callback lets the GUI present a word selection dialog between stages 2 and 3.

The offline dictionary also participates in stage 1 when available: `service_factory` injects `DefinitionService.offline_terms_exist` into the parser, whose `CompoundDictionaryMatcher` (`services/compound_matcher.py`) merges adjacent MeCab tokens into a single word whenever the joined form — with the tail token deinflected via UniDic orthBase — is an exact dictionary headword (Yomitan's longest-match principle; fixes fragment mining like 走り出した→走り). With no offline dictionary, stage 1 is unchanged.

## Package Dependencies

```
gui/                          ← sole entry point
  │
  ▼
orchestration/
  │
  ▼
services/  (+ services/dictionary/providers/)
  │
┌───────┼───────┐
▼       ▼       ▼
interfaces/ models/ utils/
        │
        ▼
     models/

config/      ← used by all packages
exceptions/  ← used by all packages
presenters/  ← NullPresenter; the GUI has its own
diagnostics/ ← inert; imported only by the GUI's export action
resources/   ← packaged data (wordsets, etc.), no code
```

Leaf packages (`config`, `models`, `exceptions`, `utils`) have no internal dependencies. `interfaces` depends only on `models` for type signatures. `services` depends on `interfaces`, `models`, `config`, `exceptions`, and `utils`. `orchestration` composes services. `gui` is the sole top-level entry point.

## Core Abstractions

Five protocols in `interfaces/` define the system's extension points:

**PresenterProtocol** (`interfaces/presenter.py`): output abstraction with 8 methods.
- `show_info`, `show_success`, `show_warning`, `show_error`: message display.
- `show_stage(index, total, name)`: pipeline stage announcement.
- `show_validation_result(ValidationResult)`: system check results.
- `show_processing_result(ProcessingResult)`: episode processing summary.
- `show_run_details(ProcessingResult)`: per-run detail lines.

Implementations: `GUIPresenter` (Qt signals) and `NullPresenter` (tests). The protocol is preserved even without a CLI so that workers, orchestration, and services stay UI-agnostic and fully testable.

**ProgressCallback** (`interfaces/progress.py`): progress reporting with 5 methods.
- `on_stage(index, total, name)`: coarse pipeline stage, independent of item progress
- `on_start(total, description)`, `on_progress(current, item_description)`
- `on_complete()`, `on_error(item_description, error_message)`

**DictionaryProvider** (`interfaces/dictionary_provider.py`): pluggable dictionary backend.
- `name` property, `is_online` property, `is_available()`, `load()`, `lookup(word) -> str | None`

**ExpressionAudioFetcher** (`interfaces/expression_audio.py`): word-pronunciation audio lookup via `fetch` and `fetch_candidates`.

**SentenceAudioFetcher** (`interfaces/sentence_audio.py`): sentence-level TTS lookup via `fetch`.

All use `typing.Protocol` for structural subtyping. Implementations satisfy the protocol via duck typing, without explicit inheritance.

## Models

Data classes in `models/`:

| Model | File | Purpose |
|-------|------|---------|
| `TokenizedWord` | `word.py` | Parsed word with surface, orth_base, lemma, reading, sentence, timing, furigana, frequency_rank, pos. `mined_form` property selects `orth_base` (source orthography, `lemma` only as a fallback when it is empty) for verbs/adjectives and surface for nouns — this is the form that becomes the Anki Expression. |
| `LineLemmas` | `word.py` | Frozen per-subtitle-line lemma set + timing; feeds the i+1 sentence filter without re-tokenizing |
| `WordData` | `word.py` | TokenizedWord + definition + media paths + pitch accent |
| `MediaData` | `media.py` | Screenshot/audio file paths and filenames |
| `ProcessingResult` | `processing.py` | Pipeline output: word counts, card count, errors, elapsed time, comprehension %, card IDs, plus the write-provenance fields (`anki_write_state`, `failure_is_transient`) |
| `MiningOutcome` | `processing.py` | Terminal classification of one non-raising `process_*` return (SUCCESS/CANCELLED/FAILED); every queue site routes on it |
| `TerminalOutcome` | `processing.py` | Whole-run outcome across items (SUCCESS/PARTIAL/FAILED/CANCELLED), computed by `classify_terminal_outcome` |
| `AnkiWriteState` | `processing.py` | What a run can *prove* about note writes: `NO_NOTE_WRITE` (the only automatically retryable state), `NOTE_WRITE_UNCERTAIN` (fail-closed), `NOTE_WRITE_CONFIRMED` |
| `ValidationResult` | `processing.py` | System check results: connectivity, tool availability, issues list |
| `ValidationIssue` | `processing.py` | Component + severity (ERROR/WARNING) + message |
| `BatchQueue` / `QueueItem` | `batch_queue.py` | Batch processing queue with PENDING/PROCESSING/COMPLETED/ERROR states |
| `MiningSession` | `stats.py` | Analytics: series, episode, word counts, timing |
| `OverallStats` | `stats.py` | Aggregated analytics |
| `DifficultyEntry` | `stats.py` | Per-episode difficulty tracking |
| `Milestone` | `stats.py` | Milestone achievement record |
| `CardPayload` | `card_payload.py` | Assembled Anki note fields + media for one card |
| `MiningQueue[ItemT]` / `ReadyItemStatus` | `mining_queue.py` | Generic queue container + the shared READY/PROCESSING/COMPLETED/ERROR status used by every queue whose items enter already mineable |
| `YouTubeQueueItem` / `YouTubeItemStatus` / `YouTubeQueue` | `youtube_queue.py` | YouTube mining queue; the one queue with its own status enum, because a URL must be probed before it is mineable |
| `AudiobookQueueItem` / `AudiobookQueue` | `audiobook_queue.py` | Audiobook mining queue; items carry `ReadyItemStatus` |
| `ReadingQueueItem` | `reading_queue.py` | Reading (manga/novel/subtitle/text) mining queue item; carries `ReadyItemStatus` |
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

- **SubtitleParserService**: parses ASS/SRT/SSA files via `pysubs2`, tokenizes Japanese text with `fugashi` (MeCab wrapper), generates furigana annotations against `TokenizedWord.mined_form` (source-orthography dictionary form for verbs/adjectives, surface for nouns), and deduplicates emitted words by `mined_form`. The token-shaping itself — mining-base selection, prefix / noun-suffix / verb-nominalizer compound merges, synthetic tokens, reading attestation — lives in `services/morphology.py`, which the parser drives.
- **WordFilterService**: multi-layer filtering, applied in this order: `partition_whitelisted` (force-included words split off first; they bypass every optional coverage filter), then `filter_unknown`, `filter_by_frequency`, `filter_by_word_lists` (blacklist only — the whitelist was already consumed), `filter_by_script_type`, `filter_by_wordsets`, `deduplicate_by_sentence`, `filter_by_sentence_length`, `filter_i_plus_one`, `filter_by_episode_count`. Everything keys on `mined_form`, the same string written to the Expression field and the one Anki dedups on. `attach_sentence_candidates` and `attach_occurrence_counts` annotate rather than filter. The name-wordset step runs in `_phase2_filter` and is gated on `bypass_optional_filters`; `config.excluded_wordsets` picks which of the bundled JMnedict-derived lists (`anki_miner/resources/wordsets/`) are active, via `services/wordset_service.py`.
- **MediaExtractorService**: extracts screenshots and audio clips at subtitle timestamps, in parallel via a `ThreadPoolExecutor` of `max_parallel_workers` threads, auto-detecting the Japanese audio stream with `ffprobe` behind a thread-safe cache. The audio encoder follows `config.audio_format`; optional animated screenshots use `libsvtav1` (AVIF) or `libwebp_anim` (WebP), each probed for availability first.
- **DefinitionService**: orchestrates the provider chain built by `DictionaryRegistry` from `config.dictionary_chain`. First-hit-wins across offline `IndexedDictProvider` instances, with an enabled `JishoProvider` entry as the online fallback. Returns HTML-formatted definition strings.
- **AnkiService**: AnkiConnect HTTP API wrapper (localhost:8765). Key operations: `verify_card_target` (pre-flight — checks the note type, validates the field mapping, and asserts the target deck already exists; it creates **nothing**, `ensure_deck` is Deck-Builder-and-Deck-Filter-only), `get_existing_vocabulary`, `create_cards_batch` (batch size 100), `delete_notes`. Stores `last_created_note_ids` for undo and `anki_write_state` as the run's write provenance. Three collaborators are split out so they are testable without HTTP mocks: `services/_ankiconnect.py` (the shared `post_action` transport, a deliberately stable patch target), `services/anki_note_builder.py` (CardPayload → note dict), and `services/anki_media_store.py` (chunked `storeMediaFile` uploads with per-file fallback).
- **ValidationService**: checks AnkiConnect connectivity, ffmpeg presence, deck existence, and note type existence. Returns `ValidationResult` (never raises).
- **YouTubeFetcherService** (`services/youtube_fetcher.py`): wraps the `yt-dlp` subprocess. Three entry points — `probe_metadata` (one video), `probe_playlist` (flat, one entry past the cap so callers can detect an over-cap playlist), and `fetch_video`, which writes the (video, subtitle) pair into a caller-owned workspace. `fallback_allowed` lets a `manual_only` fetch fall back to auto-captions only when the probe already certified them native (`_has_native_auto_ja`). Tracks the `Popen` handle so cancellation can kill the whole process tree via `psutil` — yt-dlp spawns ffmpeg as a child, and terminating the parent alone leaks it on Windows.

**Optional services (created based on config flags):**

- **MultiFrequencyService** (`services/frequency/`): **additive** aggregator over an ordered chain of SQLite-indexed sources built by `FrequencySourceRegistry` from `config.frequency_chain`. `lookup_all(word)` returns the per-source breakdown the card displays; the best (lowest) rank used for filtering and sorting comes from `min_rank(lookup_all(word))`, with `harmonic_rank` as the sort field.
- **MultiPitchAccentService** (`services/pitch_accent/`): **first-hit-wins** aggregator over `config.pitch_chain`, built by `PitchSourceRegistry` — deliberately unlike the frequency chain. The first enabled source whose three-tier `lookup_entry` resolves wins, and later sources only fill what earlier ones miss. Each index is read fully into memory on `load()` and its connection closed; the SQLite file exists for the shared recovery substrate, not for per-lookup queries.
- **ASR transcription** (`services/asr/`): offline speech-to-text with two interchangeable backends — `faster-whisper` and whisper.cpp via `pywhispercpp`. `transcriber.py` runs the model, `model_manager.py` and `ggml_model_installer.py` handle in-app model and acceleration-pack downloads, and `_engine.py` probes what is actually loadable (both backends, plus the CUDA and Vulkan native libraries) so the app degrades gracefully when the `[asr]` extra is absent. `config.asr_device` accepts `auto`, `cuda`, `vulkan`, or `cpu`; `auto` falls back down that order when a GPU path proves unusable at load time. Feeds the Utilities → Generate tab.
- **Subtitle retiming** (`services/subtitle_retimer.py`): the module-level `retime_subtitle()` function (with the `_run_alass` helper) realigns an out-of-sync subtitle file to a video via the external `alass` binary, resolved by `utils/alass_resolver.py` and installed in-app by `services/alass_installer.py`. Feeds the Utilities → Retime tab.
- **Audio condensing** (`services/audio_condenser.py`): `AudioCondenserService` builds dialogue-only condensed audio from a media file plus its subtitles — kept intervals (padding, gap-merge, offset, line filtering) computed as pure interval math, then extracted, concatenated, and re-encoded through ffmpeg, optionally with a re-timed `.srt`+`.lrc`. Takes an external subtitle file or an embedded text track. Feeds the Utilities → Condense tab; output tagging is `services/audio_tagger.py`.
- **Reading sources** (`services/reading/`): parses mokuro-processed manga volumes and Japanese books into `ReadingDocument`s. `detector.py` classifies a path and `load()` dispatches to `mokuro_source.py`, `epub_source.py`, or `aozora_source.py`; `sentence_splitter.py` segments text and `images.py` materializes page/cover images. A `.cbz`/`.zip` resolves through its sibling `.mokuro` sidecar, else an embedded `.mokuro` member read in-memory under a size cap — never extracted. Anki Miner consumes mokuro's existing OCR and does none itself. DRM-protected EPUBs are rejected up front.
- **KnownWordDB**: SQLite-backed persistent known word cache. Supports differential sync with Anki vocabulary.
- **WordListService**: loads blacklist/whitelist text files for word filtering.
- **StatsService**: SQLite-backed analytics (`mining_sessions`, `series_difficulty` tables). Provides aggregated stats and milestones.
- **UpdateChecker**: queries the GitHub Releases API for newer versions.
- **ExportService**: exports results to CSV, TSV, or vocabulary list formats.
- **ShortcutService** (`services/shortcut_service.py`): cross-platform desktop shortcut creation behind Tools → Create Desktop Shortcut — Linux `.desktop`, Windows `.lnk`, macOS informational only. (Keyboard shortcuts are unrelated and live in `gui/utils/keyboard_shortcuts.py`.)

**Card maintenance (existing notes):** two GUI-free, cancellable services that reach back into already-mined cards, both generalizing the same enumerate → chunk → `notesInfo` → compute → `updateNoteFields` loop.

- **Card backfill** (`services/card_backfiller.py`, Utilities → Card Backfill): after the user installs pitch/frequency/dictionary sources, proposes values for the fields old cards are missing. `scan_backfill` is read-only and produces the `BackfillPlan` the user approves; `apply_backfill` writes the plan's **precomputed** values — what was previewed is exactly what is written, no recompute — with a per-chunk staleness recheck, then tags touched notes `anki-miner::backfill`. Field computation deliberately mirrors `EpisodeProcessor`'s recipes, keying included; changing one means changing the mirror.
- **Card restyle** (`services/card_restyler.py`, Tools → Restyle Mined Cards): re-applies the current self-contained glossary styling to cards mined under an older format. Selection is markup-gated and idempotent, so a re-run is a no-op; it never touches note-type styling.

**Resource acquisition and recovery:**

- **Resource catalog / downloader** (`services/resource_catalog.py`, `services/resource_downloader.py`): the catalog is pure data — the recommended dictionary/frequency/pitch downloads, each tagged with a `kind` the download worker dispatches on. The downloader streams a URL to a uniquely-named `.part` file in a caller-provided directory and returns that path; it never writes the final destination, and unlike the audio fetchers it raises on failure.
- **Download resume** (`services/download_resume.py`): durable partial-download state — a `.part` body plus an atomic manifest under `runtime_state/downloads/`. Resume happens only when the server *proves* the artifact is unchanged; every ambiguity discards the partial and refetches from byte zero. The exact validator rules are in the module docstring.
- **Store recovery** (`services/store_recovery.py`, `services/startup_store_recovery.py`): the indexed stores (dicts, freqs, pitch, audio packs) share one recovery substrate — a pure per-slot decision over backup/tombstone/quarantine residue, run as one lock-gated repair and garbage-collection pass at boot.

**Dictionary providers** (`services/dictionary/providers/`):

- **IndexedDictProvider**: SQLite-backed offline provider used by every on-disk dictionary (JMdict and user-loaded Yomitan dicts). On first launch, JMdict XML is migrated to a SQLite index at `~/.anki_miner/dicts/jmdict-english/index.sqlite`; lookups run against that index. The read-only connection is opened with `check_same_thread=False` so a single instance is safe to share across worker threads.
- **DictionaryRegistry**: scans `config.dicts_root` (`ANKI_MINER_HOME/dicts/`) for installed dictionaries and builds the enabled provider chain from `config.dictionary_chain`. Disk I/O happens in the explicit `load()` call, not in `__init__`. Enabled providers remain in configured order; disabled entries are skipped.
- **JishoProvider**: opt-in REST client for the jisho.org API, disabled in the default `dictionary_chain`. Rate-limited with a configurable delay (`jisho_delay`).

**Card styling** (`services/dictionary/`): glossary CSS is emitted *inside each styled field* as a self-contained trailing `<style>` block, never as note-type CSS — the Yomitan model, so styling travels with the note (any note type, AnkiDroid, exports, shared cards) and nothing can strip or de-sync it. `card_style_presets.load_glossary_css` loads the one bundled universal stylesheet, `dict_css_scope.py` scopes a Yomitan dictionary's own `styles.css` by prefixing every top-level selector with that dictionary's stable ID, and `card_style_block.py` composes the two. Two placement invariants are load-bearing for JS-driven note types that round-trip fields through `DOMParser`: the block is **per field, not per card**, and **trailing, not leading** — a leading `<style>` gets head-hoisted and lost from `body.innerHTML`. `card_restyler.py` and `card_backfiller.py` write the same markup and must converge on it byte-for-byte.

**Expression audio** (`services/audio_packs/`, `services/expression_audio_fetcher.py`, `services/google_translate_audio_fetcher.py`, `services/custom_audio_fetcher.py`):

Word-level audio runs through a `ChainedExpressionAudioFetcher` that walks an ordered list of `ExpressionAudioFetcher` implementations (protocol in `interfaces/expression_audio.py`; fetchers never raise) and returns the first non-None path. `service_factory` assembles the chain from `config.expression_audio_chain` — `AudioSourceEntry` objects tagged `kind: "pack"|"jpod101"|"googletts"|"custom"|"custom_json"`, each with an enabled flag. The default chain is JPod101 plus a disabled Google Translate entry, so users who import nothing see pre-feature behavior with no extra I/O.

Every fetcher keys on `mined_form` + kana reading, not lemma, and skips the fetch outright on an empty reading — a reading-less lookup degrades to wildcard row selection and would cache the wrong homograph's pronunciation permanently. Hits are copied into per-source caches under `audio_cache/` (see Data Storage) rather than referenced in place, so what Anki stores is always a file the app owns.

Local packs are imported from [local-audio-yomichan](https://github.com/themoeway/local-audio-yomichan)-compatible directories; `services/audio_packs/formats.py` detects five physical layouts (`ozk5`, `ajt`, `nhk16`, `forvo`, `jpod_legacy`) and `importer.py` stages each into a SQLite index at `audio_packs_root/<pack_id>/index.sqlite`. The audio files themselves never move — entries store a pack-relative path plus the absolute `pack_dir` they resolve against. `AudioPackRegistry` keeps `__init__` I/O-free and does its scanning in `load()`.

The gate is the field mapping, not a separate flag: expression audio is written only when a fetcher is injected **and** `config.anki_fields["expression_audio"]` is non-empty. That field defaults to `""`, so the feature is off until the user maps it — the same activation pattern as frequency and pitch.

**Sentence TTS for reading sources** (`services/sentence_tts_fetcher.py`, protocol in `interfaces/sentence_audio.py`): reading-mined cards have no source audio, so `process_reading` can synthesize the card sentence instead. A `ChainedSentenceAudioFetcher` walks Google Translate TTS then Naver Papago (an unofficial scraped endpoint — the never-raises contract is load-bearing because it may drift). Assembled by `service_factory._build_sentence_audio_fetcher` from a master `reading_tts_enabled` bool (off by default) plus one per provider, in fixed Google-first order, and gated by `EpisodeProcessor._reading_tts_active` — the video, YouTube, and audiobook paths never consult it. Cache keys are content hashes, so one run synthesizes each unique sentence once and shares the file across cards.

The import flow is `gui/controllers/audio_pack_import_flow.py`, driving `gui/widgets/panels/audio_pack_settings_panel.py`. Newly imported packs are inserted above the JPod101 chain entry in a fixed pack-id priority (nhk16 > shinmeikai8 > forvo > jpod > jpod_alternate, `_PACK_PRIORITY`) — a pack-id ordering, distinct from the physical-format list above.

## Orchestration

**EpisodeProcessor** (`orchestration/episode_processor.py`):
- Receives all services via constructor injection
- `process_episode(video_file, subtitle_file, progress_callback, curation_callback, episode_name_override, series_name_override, audio_track_override, source_label_override, audio_only, cancel_event)` runs the 5-stage pipeline. `audio_only=True` is the Audiobook path (no per-word screenshots); `audio_track_override` pins a specific audio stream; `source_label_override` names the source on the card.
- `_run_pipeline(ctx, cancel_event, body)` is the shared run skeleton both entry points wrap: pre-flight gates (dictionary staleness, card-target verify, offline dictionary — all outside the `try` so a `SetupError` propagates instead of collapsing into a "completed" result), per-run temp allocation, the Anki accumulator reset, the `_external_cancel` bridge, and the try/except/finally tail. Path-specific work lives in the caller's `body` closure.
- `_stamp_write_provenance(result, failure=...)` is the single funnel every returned `ProcessingResult` passes through. It stamps `anki_write_state` from the live `AnkiService` (fail-closed to `NOTE_WRITE_UNCERTAIN` for anything that is not a real `AnkiWriteState`) and `failure_is_transient` from the raised exception — the two fields automatic retry consumes.
- `orchestration/audio_stage.py` (`AudioStage`) owns the expression-audio and sentence-TTS fetch loops and their progress-band accounting. It is the one cluster lifted out of the phase methods because it touches no pipeline ctx; `EpisodeProcessor` still constructs and closes the fetchers.
- `process_youtube_url()` calls `YouTubeFetcherService.fetch_video`, then delegates to the unchanged `process_episode` with `episode_name_override=f"YT:{video_id}"` and `series_name_override="YouTube"`. The workspace is allocated and cleaned by the worker, not the orchestrator.
- `process_reading()` mines mokuro manga volumes and Japanese novels. It reuses `_phase2_filter`, `_phase4_lookup`, and `_phase5_create` but swaps the video media stage for `_phase3_reading_media`, which materializes each word's page/cover image and expression audio (no ffmpeg, no sentence audio). Between filtering and media it applies a `reading_min_occurrence` floor (`WordFilterService.filter_by_episode_count`) that drops words appearing fewer than the configured number of times in the volume (1 = off); force-included words bypass the floor.
- Cancellation checkpoints between each phase (`self._cancelled` flag); the YouTube flow additionally threads a `threading.Event` into the fetcher so an in-flight yt-dlp subprocess can be killed. A `curation_callback` between stages 2 and 3 lets the GUI put a word-selection dialog in the way. Stats and the known-word DB are written after a successful run, and temp media is cleaned in `finally`.

**Batch processing** (`gui/workers/batch_queue_worker.py`):
There is no separate folder-orchestrator class; batch mining is driven directly by `BatchQueueWorkerThread` (a `CancellableWorker`). For each `BatchQueue` item it pairs files via `FilePairMatcher.find_pairs_by_episode_number(video_folder, subtitle_folder)` (episode-number matching across two folders, not stem-name matching). A per-item config copy with the item's `subtitle_offset` is made via `dataclasses.replace`. Per-pair failures are surfaced individually (the item is marked ERROR with a count) since `process_episode` returns failures as results rather than raising.

**Season-level curation** (`services/word_pool.py`): with per-episode curation, a 12-episode season means 12 dialogs. Season mode makes it one. The worker runs the pairs twice: a capture pass where a `CaptureCurationCallback` collects each episode's candidate words and creates zero cards, then `merge_pools` unions them into a single series-wide pool, one curator dialog over that pool, and `split_selection` hands each episode back only its share of what the user kept before the mining pass runs for real. `ManualPairWorkerThread` uses the same helpers. Non-season batch runs stay single-pass.

**DeckBuilderWorker** (`gui/workers/deck_builder_worker.py`):
Whole-series deck mining in two phases separated by a GUI confirm gate.

Phase 1 — aggregate + select: `SubtitleParserService.count_lemmas` is called on every subtitle in the request. The raw per-file counters are summed by `services.corpus_aggregator.aggregate` into a single corpus `Counter`. `select` then ranks lemmas by in-corpus frequency and picks a candidate set according to the mode (ALL, TOP_N, COVERAGE_PCT). Coverage is computed over in-corpus mineable-word token counts (the same POS-filter as mining applies), not `frequency.csv`. A `DeckBuildPreview` is emitted and the worker blocks on a `threading.Event` gate until the GUI calls `confirm()` or `reject()`.

Phase 2 — build: `AnkiService.ensure_deck` creates the target deck if it does not exist (idempotent), and must run *before* the per-pair loop — `process_episode`'s pre-flight only asserts the deck exists. For each episode pair, a fresh `EpisodeProcessor` is created via `dataclasses.replace(config, anki_deck_name=deck_name, include_known_words=not collection_filter, bypass_optional_filters=True, allow_duplicate_cards=True)` — no production code other than the config fields changes. The last two are the load-bearing invariant: the per-episode reduction filters (i+1, frequency, word lists, sentence dedup/length) and Anki-side dedup must not run, or the build silently delivers a fraction of the deck the preview promised. Known-words subtraction is the one filter that stays, gated on the collection checkbox. A `curation_callback` closure keeps a word only if its lemma is in the selected set and has not already been carded in a previous episode (`carded: set[str]` shared across the loop). This enforces the cross-episode "card each lemma once" invariant without touching `EpisodeProcessor` internals. `episode_name_override` and `series_name_override` are set to the video stem and deck name respectively so analytics rows are distinct from regular episode-mining sessions.

## Configuration

`AnkiMinerConfig` (`config/config.py`) is a frozen (immutable) dataclass of roughly 110 fields. Grouped by area (not every field is listed):

- **Anki:** deck name, note type, field mappings, AnkiConnect URL
- **Media:** audio padding, screenshot offset, temp folder, subtitle offset (range ±300s), `ffmpeg_location` / `ffprobe_location` (explicit binary paths consumed by the resolver — see [ffmpeg / ffprobe](#ffmpeg--ffprobe))
- **Filtering:** min word length, allowed POS tags, excluded subtypes, deduplication, `exclude_hiragana_only_words` / `exclude_katakana_only_words` (kana-only drops, default off), `excluded_wordsets` (active bundled JMnedict name wordsets), `reading_min_occurrence` (per-volume minimum word occurrence for the Reading tab; 1 = off)
- **Dictionary:** `dictionary_chain` (the runtime-authoritative ordered list of providers — indexed dicts and Jisho, each toggleable), `dicts_root` (root for all installed `.sqlite` indexes; defaults to `ANKI_MINER_HOME/dicts/` via the `ANKI_MINER_HOME` constant in `config/paths.py`), Jisho URL/delay. Legacy `jmdict_path` is retained for the first-launch JMdict-XML migration only (`use_offline_dict` and the pre-v2.5 migration shims are gone; `gui_config.json` now carries a `config_schema_version` stamp).
- **Frequency:** `frequency_chain` (ordered tuple of `FreqEntry(source_id, enabled)` — the runtime-authoritative chain of frequency sources), `freqs_root` (root for the per-source `index.sqlite` files; defaults to `ANKI_MINER_HOME/freqs/`). The `frequency_sort` `anki_fields` entry writes the chosen sort value to its own card field.
- **Expression audio:** `expression_audio_chain` (ordered `AudioSourceEntry` list), `expression_audio_delay`, `audio_packs_root`
- **ASR (subtitle generation):** `asr_model`, `asr_device`, `asr_models_root`, `cuda_libs_root`, `onnx_pack_root`
- **YouTube:** `youtube_cookies_from_browser` (browser profile to pull cookies from) / `youtube_cookies_file` (explicit cookies file), max duration, subtitle mode
- **Appearance:** `theme`, `theme_favorites`, `themes_root`, `ui_language`, and two distinct scaling knobs, both clamped to [0.5, 2.0] — `ui_font_scale` (text only, applied live through the QSS `${font-size-*}` variables) and `ui_zoom` (the whole UI via `QT_SCALE_FACTOR`, which needs a restart to take effect)
- **Optional data:** pitch accent, frequency, known words DB, blacklist/whitelist paths and toggles
- **Analytics:** stats DB path
- **Performance:** max parallel workers (default 6)

The `__post_init__` method uses `object.__setattr__` to convert string paths to `Path` objects (required because the dataclass is frozen). New config instances are created with `dataclasses.replace()`.

**Config source:**
- GUI: `GUIConfigManager` (`gui/utils/config_manager.py`) persists to `~/.anki_miner/gui_config.json`. Defaults come from the `AnkiMinerConfig` dataclass field defaults.

**Named profiles.** `gui_config.json` stays the single live config; profiles are full-config sidecar snapshots in `profiles/<id>.json`, written by `gui/utils/profile_store.py` (storage only, Qt-free). There is deliberately **no index file** — the directory listing enumerates profiles, each file carries its own display name, and the active id is a marker inside `gui_config.json`; an index would duplicate both and buy a class of marker-vs-index divergence bugs. `gui/controllers/profile_controller.py` owns the *ordering* of a switch, which is where the data-loss paths sit: snapshot the outgoing profile first, save an unattributable live config as a **new** profile rather than adopting an existing id (profile files have no `.bak`), read the incoming file before advancing the pointer, and roll the pointer back if the commit fails. Machine-local runtime state is structurally excluded from profiles and settings export, because both serialize only `AnkiMinerConfig`.

**UI language.** `config.ui_language` (normalized to lower-case, empty → `"en"`) selects the catalog `gui/i18n.py` installs at startup — the app's own `.qm` plus Qt's bundled `qtbase_<lang>.qm` for standard dialog buttons and file pickers — before any widget is constructed. `"en"` is the source language and installs nothing. Catalogs are extracted and compiled by `scripts/i18n.py` (`extract`, `compile`); this is a manual step, not CI-gated.

## GUI Architecture

### Window Structure

`MainWindow` contains a `QTabWidget` with seven tabs (registered in `gui/app.py` as Video, Deck Builder, Audiobooks, Reading, Analytics, Utilities, Settings). Every container tab carries a stable `_subtab_index` dict mapping string keys to inner-tab indices and a duck-typed `open_subtab`, so `capabilities.SUBTAB_KEYS` can address a sub-tab by name and the Usage Guide can reveal it (indices are never used as the identity).

Mining screens share a base chain in `gui/widgets/`. `MiningTabBase(TaskPublisherMixin, ScreenIssueHost, QWidget)` is the root — `SingleEpisodeTab`, `BatchProcessingTab`, and `DeckBuilderTab` subclass it directly. The queue-driven screens go one level deeper through `_QueueMiningTabBase`, which splits into `_ListQueueMiningTabBase` (the `QListWidget`-queue screens: `YouTubeTab`, `AudiobookTab`) and `_ReadingMiningTabBase` (the four Reading sub-tabs). `TaskPublisherMixin` is what makes every one of them publish into the task registry.
1. **VideoTab** (`gui/widgets/video_tab.py`, "Video") — container over **Single** (`SingleEpisodeTab`, one video/subtitle pair), **Batch** (`BatchProcessingTab`, two folders paired by episode number), and **YouTube** (`YouTubeTab` → `YouTubeQueueWorker`). URL classification (plain video, playlist, video-in-playlist, Mix) happens without network access in `utils/youtube_url.classify_youtube_url`; playlists resolve through `YouTubePlaylistResolveWorker` then `YouTubePlaylistProbeWorker`, with an over-cap confirm past `youtube_playlist_max`. Each sub-tab keeps its own presenter and worker lifecycle; the container fans out config/shutdown and exposes live workers via `iter_close_workers`.
2. **DeckBuilderTab** — whole-series deck mining over a corpus of subtitles, driven by `DeckBuilderWorker` (see Orchestration). Two phases separated by a GUI confirm gate.
3. **AudiobookTab** (`gui/widgets/audiobook_tab.py`, "Audiobooks") — local audio + subtitle pairs, so items enter the queue READY with no probe stage. Mining runs `process_episode(audio_only=True)`: no per-word screenshots, embedded cover art extracted once per book and shared as every card's Picture, and the keep/drop decision keyed on audio clip success. Stats identity is `series_name_override="Audiobook"` + the audio file stem.
4. **ReadingTab** (`gui/widgets/reading_tab.py`) — container over **Manga** (mokuro volumes, or a series folder expanded into per-volume items), **Novels** (one `.epub`/`.txt`, or a non-recursive folder scan via `detector.detect_book_folder`), **Subtitles** (standalone subtitle files, no video, rows removable mid-run through `ReadingQueueWorker.skip_item`), and **Text** (pasted text — the one reading source that builds its own pathless `ReadingSourceRef` and never touches `detector`). All four subclass `_ReadingMiningTabBase` → `_QueueMiningTabBase` → `MiningTabBase` and run `EpisodeProcessor.process_reading`.
5. **AnalyticsTab** — mining statistics dashboard over `StatsService`.
6. **SubtitlesTab** (`gui/widgets/subtitles_tab.py`, "Utilities") — container over five tools in two shapes. Three are file-queue tools on `FileQueueWorker`: **Generate** (`SubtitleCreationTab` → `services/asr/`), **Retime** (`SubtitleRetimeTab` → `services/subtitle_retimer.py`), **Condense** (`CondenseTab` → `services/audio_condenser.py`). Two reach into an existing Anki collection through a read-only scan worker the user approves before an apply worker writes: **Card Backfill** (`CardBackfillTab` → `services/card_backfiller.py`) and **Deck Filter** (`DeckFilterTab` → `services/deck_filter.py`, which copies a premade deck's surviving notes into a new deck).
7. **SettingsTab** (`gui/widgets/settings_tab.py`) — a grouped nav list driving a `QStackedWidget`: 5 groups, 10 destinations, each with a stable key (`anki`, `media`, `dictionaries`, `audio`, `frequency`, `pitch`, `filtering`, `youtube`, `subtitles`, `ui`) that `reveal_setting` and the capability browser address it by. `gui/widgets/settings_search.py` indexes the anchors registered by `gui/widgets/base/setting_anchor.py` (built after the Qt translators install, so the index is localized) and jumps to a result — a jump aid, never a filter. Panels live in `gui/widgets/panels/`. The tab emits `config_changed`; `MainWindow` stamps and saves, then fans the committed object back out via `config_refreshed` so a stale worker snapshot cannot regain authority.

### Worker Threads

`CancellableWorker` (`gui/workers/base_worker.py`, QThread + `threading.Event`) provides:
- Thread-safe cancellation via `cancel()` / `is_cancelled()` / `check_cancelled()`
- Qt signals for results, errors, and progress

Two intermediate bases sit on it, both in `base_worker.py`:
- `ProcessorOwningWorker`: for workers driving an `EpisodeProcessor`. Declares the typed `curation_processor` contract (so GUI readers can't silently `getattr`-miss it) and the exactly-one-of `processor`/`processor_factory` constructor check.
- `SingleCallWorker`: one blocking call, one `result_ready` emission — the shape behind the short-lived AnkiConnect fetch workers.

Two shared spines sit above those, so a new queue screen inherits its contract instead of inventing one:

- `_queue_worker_base.py` (`SequentialQueueWorker`) drives the three sequential mining queues — YouTube, Reading, Audiobook — with a frozen item snapshot, an identical four-signal shape, a staleness pre-loop gate, a deferred factory build, and retry backoff. `RunBoundaryControls` in the same module owns Pause / Finish-current semantics.
- `file_queue_worker.py` (`FileQueueWorker`) does the same for the file-processing tools (subtitle generate / retime / condense), which share a byte-identical five-signal contract.

The rest of `gui/workers/` is one file per job and `ls` is the index. The shapes worth knowing before adding one:

- **Mining**: `EpisodeWorkerThread` (one pair), `BatchQueueWorkerThread` and `ManualPairWorkerThread` (many pairs, both with the season two-pass path), `DeckBuilderWorker` (see Orchestration).
- **Scan-then-apply over an existing collection**: `BackfillScanWorker`/`BackfillApplyWorker`, `DeckFilterScanWorker`/`DeckFilterApplyWorker`. The scan is read-only and produces a plan the user approves; the apply writes exactly that plan's precomputed values.
- **Unified installers and importers**: `ImportWorker` (dictionary, frequency, pitch, audio packs — per-domain `for_*` factories; user cancel routes to a distinct `cancelled` signal, never `failed`) and `InstallWorker` (ASR models, CUDA/ONNX packs, external binaries).
- **Short-lived AnkiConnect fetches**: `FetchDecksWorker` / `FetchNotetypesWorker` / `FetchFieldsWorker`, each a `SingleCallWorker` around one getter.
- **Window-level background work**: `ValidationWorkerThread`, `UpdateWorkerThread`, `YtdlpUpdateWorker`, `PrewarmWorker` (warms `fugashi.Tagger()` and the dictionary indexes right after first paint, so the first Mine click does not build them on the GUI thread).

Beyond QThreads, `gui/utils/run_off_thread.py` provides `run_off_thread` for one-off blocking work in a GUI slot (worker ownership is automatic so it cannot be GC'd mid-run) plus `still_running` / `join_or_retain` for deleted-wrapper-safe bounded joins. Offloading is an **enforced convention, not an option**: `gui/utils/stall_watchdog.py` runs by default in the shipped app (250 ms heartbeat QTimer + daemon monitor thread) and logs a WARNING with the GUI thread's stack trace whenever the event loop goes stale, so a re-introduced blocking slot surfaces in the log instead of as an unexplained freeze.

### Signal Architecture

`GUIPresenter` emits Qt signals from worker threads. Main window slots receive them on the GUI thread. Per-tab presenters avoid cross-tab signal pollution. `GUIProgressCallback` bridges the `ProgressCallback` protocol to Qt signals.

GUIPresenter does **not** explicitly inherit from `PresenterProtocol`. It satisfies the protocol via structural subtyping, which avoids a metaclass conflict between `QObject` and `Protocol`.

Keyboard shortcuts go through `gui/utils/keyboard_shortcuts.py`, which enforces three rules a raw `QShortcut` does not: every shortcut is scoped to its owner (the default `WindowShortcut` context fires from hidden sibling pages), confirmation is `Ctrl+Return`/`Ctrl+Enter` and never bare Enter (Japanese IMEs commit composition with Enter), and the global sequences live there as constants that the About card's shortcut table is generated from. Shortcuts are parented to their owning widget so Qt retains them.

### Task Registry and Run Receipts

Progress has exactly one owner. Three pieces in `gui/controllers/` hold it, without moving worker ownership (which stays with the tab that spawned it):

- **`task_registry.py`**: `TaskRegistry` is the one authoritative record of what the app is doing, and owns the one-second tick. Producers write through a `TaskHandle` carrying a run token, so a late signal from a finished run cannot overwrite the run the user is watching. Views render immutable `TaskSnapshot`s and hold no progress state of their own. Two honesty rules are enforced here rather than at each call site: `fraction` is `None` unless a real denominator exists (a synthetic blended percentage is what produces a frozen-looking progress bar), and the elapsed clock is driven by the tick rather than by producer updates, with `no_update_age_s` stating the silence actually observed — neither asserts the worker is alive.
- **`run_receipt.py`**: `RunReceiptAccumulator` — one per run, fed by the same per-item signals the screen already handles, outliving both the progress bar and the worker, so a run's numbers survive the cancel or failure that resets the bar. A cancelled run still reports what it finished; the counts are *notes*, not cards; elapsed is active time (`gui/utils/progress_telemetry.active_duration`), so a run spanning a laptop sleep does not claim to have worked through it.
- **`background_tasks.py`**: `BackgroundTaskController` owns the four window-level worker handles (validation, update check, JMdict migration, prewarm) and the single shutdown join policy `closeEvent` routes every owned and tab-owned worker through. Lifecycle only — results flow back to `MainWindow` via forwarding signals, and all UI consumption stays there.

`TaskPublisherMixin` (`gui/widgets/base/task_publisher.py`) is a base of `MiningTabBase`, so every mining screen publishes into the registry; `bind_task_registry` wires the list queues in `gui/app.py`.

### Session Recovery

Two kinds of state deliberately survive quitting, both under `runtime_state/` (machine-local, and structurally excluded from settings export and profiles because those serialize only `AnkiMinerConfig`):

- **Queue contents** (`gui/utils/queue_state_store.py`): a bounded, versioned, atomically-replaced JSON snapshot per queue, written on close. Only immutable facts are stored — a path, a URL, a status, a count; never a worker, processor, workspace, or fetched media file. A row that was running comes back as `STATUS_INTERRUPTED`, an unknown rather than a failure, so nothing about it is automatically re-run. A pasted-text reading source is a form draft and is refused outright.
- **Partial downloads** (`services/download_resume.py`, see Services).

`RecoveryController` (`gui/controllers/recovery_controller.py`) takes stock of both at next launch and asks **once**, Restore or Discard. It never restores automatically, never runs what it restored, and never re-validates a partial itself. Path derivation lives in `gui/utils/runtime_state.py`, which reads `GUIConfigManager.CONFIG_FILE` at call time rather than snapshotting it at import, so test home-isolation applies.

### Theme System

Theme singleton backed by JSON theme files in `gui/resources/styles/themes/`. 29 built-in themes: 9 named families (Ayu, Catppuccin, Dracula, Everforest, GitHub, Gruvbox, Kanagawa, Rosé Pine, Solarized) plus 6 standalone themes (Dark, Light, Nord, One Dark, Sakura, Tokyo Night) that carry no `family` field. The `discover_themes()` function scans the themes directory at startup, validates each JSON file against a required color key schema (`REQUIRED_COLOR_KEYS`), and registers valid themes. A single `common.qss` stylesheet uses `${color-*}` variable substitution. The `Theme._substitute_variables()` method merges layout variables from `_variables.py` with color variables extracted from the active theme JSON. Custom themes can be added by dropping a valid JSON file into the themes directory. The chosen theme is a config field (`config.theme`) persisted in `gui_config.json`, so it travels with settings profiles and exports; `QSettings` backs only the machine-local `ui_state.ini`. A theme routes the whole `QApplication` palette, not just the stylesheet — which is why gallery thumbnail rendering (`gui/widgets/enhanced/theme_preview.py`) must never touch `QApplication`, since Qt cannot un-set an application palette once set.

### Video Preview (embedded libmpv)

The in-app video preview runs on libmpv via the `python-mpv` binding (not Qt Multimedia — the Qt FFmpeg backend had no software AV1 decode and froze on Windows sink teardown). Three layers:

- `utils/mpv_loader.py`: the ONLY module allowed to `import mpv`. Resolves libmpv in order env override (`ANKI_MINER_LIBMPV`, fails closed) → PyInstaller-bundled library → system library, monkeypatching `ctypes.util.find_library` around the import because python-mpv dlopens at import time. It also owns the C-numeric-locale assertion before every `mpv.MPV(...)` construction (Qt stomps `LC_NUMERIC`), the `create_mpv_player` factory, `mpv_available()`, and the display-free `mpv_probe_main()` the bundle smoke drives.
- `MpvVideoWidget` (`gui/widgets/mpv_video_widget.py`): a `QOpenGLWidget` view on the libmpv render API (`render_gl.h`; works on Wayland where `wid` embedding cannot). A dumb view — owns only the render context, which MUST be freed (`detach`) before the owning `MPV` handle terminates or libmpv aborts the process.
- `SubtitlePlayerWidget` (`gui/widgets/subtitle_player_widget.py`): the controller. Owns the `mpv.MPV` handle (one per widget lifetime; re-sourcing uses `loadfile`), holds all playback policy, and bridges python-mpv's event-thread callbacks to the GUI thread via queued Qt signals (every slot None-guards — a None property value is the normal first event).

Release bundles ship libmpv from the repo-owned `vendor-libmpv-*` GitHub releases, produced by `.github/workflows/vendor-libmpv.yml`. pip and source installs use the system libmpv, and the preview pane shows a notice when none is found.

### Dialogs

All in `gui/widgets/dialogs/`.

- `WordCurationDialog`: user selects which discovered words to mine (cross-thread via a `threading.Event` bridge). Embeds `SubtitlePlayerWidget` per row for in-place audio/video preview, and renders multi-dictionary lookup via `DefinitionService.lookup_all_offline`.
- `ResultsDialog`: summary of a mining session with undo option.
- `ExportDialog`: export results to file.
- `setup_wizard/` (`SetupWizard`, `pages.py`): the guided first-run `QWizard` — AnkiConnect reachability, target deck, note type + field mapping, recommended resources. **Detect-and-guide only**: it never creates decks or note types via AnkiConnect. The user performs every Anki-side action while the wizard inspects, explains, links, and re-checks live rather than trusting a result cached when the page was built. Re-runnable from Tools → Setup Wizard.
- `SystemHealthWindow`: the permanent readiness screen, since the wizard's facts go stale the moment it closes. One parented, modeless instance for the window's lifetime, owning no worker. Every row starts *unknown* and stays unknown until something reports — a check that has not run is not a failure — and the deck/note-type/field checks are skipped rather than painted red when AnkiConnect is unreachable. `fix_requested` routes to `MainWindow.reveal_setting`.
- `CapabilityBrowser`: the "Usage Guide" (F1) over the hand-written `gui/capabilities.py` catalogue. Entries are hand-written rather than introspected from config because the value is the phrasing and the search synonyms; each `CapabilityTarget` names a main tab and optional sub-tab key from `SUBTAB_KEYS`, resolved through the container's duck-typed `open_subtab`. Titles and descriptions are `QT_TRANSLATE_NOOP`-wrapped; keywords stay untranslated because they are the search index and must match what users actually type ("i+1", "tts", "ocr").
- `ProfileManagerDialog`: create/rename/switch/delete named settings profiles over `ProfileController`.
- `ResourceDownloadWindow` / `ResourceDownloadSession`: pick and download recommended resources from `resource_catalog`, driving `ResourceDownloadWorker`.
- `KnownWordsManagerDialog`: manage the user-curated known-word list.
- `SubtitleTracksDialog` / `AudioTracksDialog` (over the shared `_track_picker_dialog.py` base): pick an embedded stream out of a media file.
- `AboutDialog`: version, credits, and the generated keyboard-shortcut table.

## External Integrations

### AnkiConnect

HTTP POST to `localhost:8765` (configurable). Protocol version 6. Key actions:
- `version`, `deckNames`, `modelNames`, `modelFieldNames`: validation.
- `findNotes`, `notesInfo`: vocabulary lookup.
- `storeMediaFile`: upload screenshots/audio.
- `addNote`, `addNotes`: card creation (batch size 100).
- `deleteNotes`: undo support.

### Jisho API

GET `https://jisho.org/api/v1/search/words?keyword=<word>`. Rate-limited with configurable delay (default 0.5s). Surfaced as `JishoProvider` inside the configurable provider chain — its position is user-controlled via `config.dictionary_chain`. It is disabled by default; when enabled in the default position, it sits after `IndexedDictProvider(jmdict-english)` as the online fallback. Users may move it ahead of any indexed dictionary.

### ffmpeg / ffprobe

- **ffmpeg:** `-ss` seek + `-i` input + `-frames:v 1` for screenshots, `libmp3lame` (mp3) or `libopus` (opus) for audio extraction per `config.audio_format`
- **ffprobe:** `-show_streams -select_streams a` for Japanese audio track detection
- Parallel execution via `ThreadPoolExecutor` (default 6 workers)

**Binary resolution.** Every ffmpeg/ffprobe invocation goes through a resolver rather than assuming a bare `ffmpeg` on PATH. Order: explicit `config.ffmpeg_location` / `config.ffprobe_location` → binaries bundled inside the frozen app → PATH. Every standalone build ships GPL ffmpeg and ffprobe (the `.deb` included, since v2.10 — it packages the same full PyInstaller tree as the AppImage); PyPI/`pipx` and source installs rely on PATH. A startup health check validates whatever was resolved and surfaces a clear error when nothing usable is found.

### yt-dlp

Subprocess invoked by `YouTubeFetcherService`. Single-video probe uses `--skip-download --dump-single-json --no-playlist`; playlist probe uses `--flat-playlist` with one item past the cap; fetch adds `--sub-lang ja --sub-format vtt/best --convert-subs srt` and a height-capped format string.

The subtitle flags carry one load-bearing invariant. `auto_only` passes `--write-auto-sub`; `manual_only` passes `--write-sub`, and adds `--write-auto-sub` **only** when `fallback_allowed` — meaning the probe already certified the auto track as native Japanese. Both flags in one invocation mean "manual preferred, auto as fallback", because yt-dlp loads manual tracks first and lets `automatic_captions` fill only the languages still missing. Passing the auto flag unconditionally would silently mine machine-translated Japanese whenever a `manual_only` video's manual track vanished between probe and fetch.

Progress is parsed from a custom `--progress-template`, with post-download phases detected from the `_POSTPROCESS_MARKERS` line signatures. Optional `--cookies-from-browser` or `--cookies` bypasses bot-detection prompts and age restrictions.

### Bundling yt-dlp

The standalone **binary** is vendored, not the Python package. Every call site spawns yt-dlp as a subprocess, so the importable `yt_dlp` module was never used at runtime; `anki_miner.spec` excludes it. It stays a pip dependency, which is how non-frozen installs get the console script that the resolver's interpreter-sibling tier finds. `.github/ytdlp-pin.json` holds the pinned version and per-OS digests, and `scripts/check_ytdlp_pin.py` gates their freshness at build time.

Installs with no bundled binary — and bundles whose pinned copy has aged out — are covered at runtime by **`services/ytdlp_updater.py`**, which auto-downloads and self-updates yt-dlp into `~/.anki_miner/bin/` behind a GitHub URL allowlist and a never-raises contract, throttled by a timestamp file and run off the GUI thread by `YtdlpUpdateWorker`. Each managed binary is written with a SHA-256 verification receipt beside it (`ytdlp_resolver.ytdlp_verification_receipt_path`) and is selected only while that receipt still matches the file's bytes; legacy pre-receipt files are never selected. Resolution order in `_compute`: config override → **receipt-verified managed copy** → PATH → bundled binary → (non-frozen) interpreter sibling, with a fail-closed raise for a PATH entry pointing at an unverified managed copy. The managed copy sits above PATH deliberately, so a completed self-update actually takes effect; PATH still outranks the build-time-pinned bundled binary.

### libmpv (video preview)

Loaded in-process through the `python-mpv` binding — see [Video Preview (embedded libmpv)](#video-preview-embedded-libmpv) for the loader/view/controller split. Distribution mirrors ffmpeg's: every standalone build (Windows `Setup.exe`, macOS `.tar.gz`, Linux AppImage and `.deb`) bundles a libmpv shared library fetched by pinned URL + SHA256 from the repo-owned `vendor-libmpv-*` GitHub releases, with GPL bookkeeping in `licenses/libmpv/`. PyPI/`pipx` and source installs resolve the system libmpv. Absence is non-fatal — `mpv_available()` gates the preview UI and a notice replaces the pane.

## Exception Hierarchy

```
AnkiMinerException (base)
├── SetupError
│   └── OperationCancelled
├── AnkiConnectionError
├── SubtitleParseError
├── SubtitleRetimeError
│   └── AlassNotFoundError
├── FfmpegNotFoundError
└── YouTubeFetchError
    ├── BotDetectionError
    ├── CookieDatabaseLockedError
    ├── VideoTooLongError
    ├── YtdlpNotFoundError
    └── NoJapaneseSubtitlesError
```

Defined across `exceptions/` (`base.py`, `validation.py`, `anki.py`, `media.py`, `subtitle.py`, `youtube.py`, `cancel.py`). `FfmpegNotFoundError` is a direct subclass of `AnkiMinerException`, not of `YouTubeFetchError`, even though only the YouTube fetch path raises it today. `OperationCancelled` is a `SetupError` so a user cancel unwinds through the same pre-flight path a setup failure does, without being reported as one.

## Data Storage

All persistent user data under `~/.anki_miner/`:

| File | Format | Purpose |
|------|--------|---------|
| `gui_config.json` | JSON | GUI configuration persistence; also carries the `active_profile_id` marker |
| `profiles/<id>.json` | JSON | Named settings profiles — full config snapshots as sidecars beside the live config. No index file: the directory listing enumerates them, each file carries its own display name (`gui/utils/profile_store.py`) |
| `recent_files.json` | JSON | Most-recent video/subtitle pairs (`gui/utils/recent_files.py`) |
| `JMdict_e` | XML | Source JMdict XML (~60MB); migrated to SQLite on first launch |
| `dicts/<dict-id>/index.sqlite` | SQLite | Indexed offline dictionaries (e.g. `jmdict-english/`); queried by `IndexedDictProvider` |
| `known_words.db` | SQLite | Known word cache with Anki sync |
| `stats.db` | SQLite | Analytics. Exactly two tables — `mining_sessions` and `series_difficulty`; milestones are derived at query time, not stored |
| `pitch/<source_id>/index.sqlite` | SQLite | Per-source pitch accent index; the runtime-authoritative first-hit-wins chain (`config.pitch_chain`) |
| `pitch_accent.csv` | CSV | Legacy single pitch file; auto-imported into `pitch/legacy-pitch/` on first launch, then no longer read (kept on disk for downgrade) |
| `frequency.csv` | CSV | Legacy single frequency list; no longer read (superseded by the `freqs/` chain — the one-time migration was removed) |
| `freqs/<source_id>/index.sqlite` | SQLite | Per-source frequency index queried by `IndexedFreqProvider`; the runtime-authoritative frequency chain |
| `audio_cache/jpod101/` | Files | JapanesePod101 expression audio cache: `jpod101_{mined_form}_{reading}.mp3` + zero-byte `.miss` negative markers |
| `audio_packs/<pack_id>/index.sqlite` | SQLite | Per-pack expression audio index; audio files stay in their original location |
| `audio_cache/local_packs/` | Files | Per-hit cache copies from installed packs: `{pack_id}_{mined_form}_{reading}{ext}` |
| `audio_cache/googletts/` | Files | Google Translate synthetic-TTS cache: `googletts_{mined_form}_{reading}.mp3` (no `.miss` markers — synthetic failures are transient) |
| `audio_cache/custom_*/` | Files | Per-source caches for the `custom` / `custom_json` URL-template audio kinds (`services/custom_audio_fetcher.py`) |
| `audio_cache/sentence_tts/` | Files | Reading-path sentence TTS: `sentencetts_{provider}_{sha1(sentence)[:16]}.mp3` (content-hash keys, no `.miss` markers) |
| `runtime_state/downloads/` | Files | Partial-download resume state: `<key>.part` bodies + `<key>.json` manifests (`services/download_resume.py`) |
| `runtime_state/queues/` | JSON | Queue-contents snapshots written on close, one file per queue (`gui/utils/queue_state_store.py`) |
| `asr_models/` | Files | Downloaded local Whisper models (Utilities → Generate); `asr_models/ggml/` holds the whisper.cpp weights |
| `cuda_libs/` | Files | In-app CUDA acceleration pack for ASR |
| `onnx_pack/` | Files | In-app ONNX/VAD pack for ASR |
| `bin/` | Files | In-app-installed external binaries (`alass`, the managed yt-dlp binary + its verification receipt) |
| `.ytdlp_update_check` | Text | Unix timestamp of the last yt-dlp update check; throttles the next one (`services/ytdlp_updater.py`) |
| `themes/` | JSON | User-added custom theme files |
| `ui_state.ini` | INI | Machine-local UI session state (window geometry, route, splitter positions, last-browsed folders) via `QSettings`. Deliberately outside `gui_config.json`, and excluded from profiles and exports |
| `instance.lock` | Lock | Single-instance guard held for the app's lifetime |
| `anki_miner.log` | Log | Application log, rotated to `.1`–`.5`. `anki_miner.crash` captures a failure early enough that logging is not up yet |

Temporary media files are stored in the system temp directory under `anki_miner_temp/` and cleaned up after each processing run. YouTube downloads go one level deeper — `anki_miner_temp/youtube/run-<uuid>/` — owned by `YouTubeQueueWorker` (one workspace per attempt; cleaned up in `finally` on every exit path) and `rmtree`'d on every exit path (success, cancel, exception). Reading (manga/novel) mining materializes each word's page or cover image into a temp workspace rather than running ffmpeg.

## Not covered here

This page maps the pipeline and the boundaries between packages. Several subsystems are big enough to have their own logic but do not change that map, so they are documented in their module docstrings instead of here. Read the module before changing one:

| Area | Where |
|------|-------|
| Token shaping — mining base, compound merges, synthetic tokens, reading attestation | `services/morphology.py` |
| Deinflection rules and the Yomitan transform table | `services/deinflection.py`, `services/japanese_transforms.py` |
| Deck Filter (copy a premade deck's worth-learning notes into a new deck) | `services/deck_filter.py`, `gui/workers/deck_filter_worker.py`, `gui/widgets/deck_filter_tab.py` |
| Season-level pooled curation | `services/word_pool.py` |
| Bundled name wordsets | `services/wordset_service.py` |
| Note type presets and field-mapping auto-detection | `services/note_presets.py` |
| Diagnostics export and log redaction | `diagnostics/bundle.py`, `diagnostics/environment.py` |
| App bootstrap and restart-to-apply | `gui/launch.py`, `gui/restart.py` |
| Condensed-audio tagging and artwork | `services/audio_tagger.py` |
| Custom URL-template audio sources | `services/custom_audio_fetcher.py` |
