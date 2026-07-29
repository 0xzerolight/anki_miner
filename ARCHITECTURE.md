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

- **SubtitleParserService**: parses ASS/SRT/SSA files via `pysubs2`, tokenizes Japanese text with `fugashi` (MeCab wrapper), generates furigana annotations against `TokenizedWord.mined_form` (source-orthography dictionary form for verbs/adjectives, surface for nouns), and deduplicates emitted words by `mined_form`. Compound-merge passes (prefix, noun-suffix, verb-nominalizer) reconstruct synthetic lemmas from each component's `feature.lemma` so dictionary lookups can hit the headword.
- **WordFilterService**: multi-layer filtering. `partition_whitelisted` runs first and splits off force-included words that bypass every optional coverage filter. The rest pass through `filter_unknown` (checked on `mined_form` — the same string written to the Expression field and the same string Anki dedups on), `filter_by_frequency`, `filter_by_word_lists` (blacklist only, matched on `mined_form` with a miss-only `lemma` fallback; the whitelist is never consulted here because `partition_whitelisted` already removed those words), `filter_by_script_type`, `filter_by_wordsets`, `deduplicate_by_sentence` (NFKC-normalized, whitespace-collapsed dedup key), `filter_by_sentence_length`, `filter_i_plus_one`, and `filter_by_episode_count`. `attach_sentence_candidates` (alternative example sentences for the curator's sentence picker) and `attach_occurrence_counts` annotate rather than filter. The name-wordset filter (Issue #59) runs in `_phase2_filter` after the blacklist/whitelist step and is gated on `bypass_optional_filters` (skipped by the Deck Builder corpus preview); which wordsets are active is controlled by `config.excluded_wordsets`. Wordset files are bundled JMnedict-derived proper-noun lists in `anki_miner/resources/wordsets/`.
- **MediaExtractorService**: extracts screenshots (`ffmpeg -frames:v 1`) and audio clips at subtitle timestamps. The audio encoder follows `config.audio_format`: `libmp3lame` for mp3 (the default), `libopus` for opus. Runs in parallel via `ThreadPoolExecutor` with `max_parallel_workers` threads. Auto-detects the Japanese audio stream via `ffprobe` with thread-safe caching. Optional animated screenshots use `libsvtav1` (AVIF) or `libwebp_anim` (WebP), each probed for availability before use.
- **DefinitionService**: orchestrates the provider chain built by `DictionaryRegistry` from `config.dictionary_chain`. First-hit-wins across offline `IndexedDictProvider` instances, with an enabled `JishoProvider` entry as the online fallback. Returns HTML-formatted definition strings.
- **AnkiService**: AnkiConnect HTTP API wrapper (localhost:8765). Key operations: `verify_card_target` (pre-flight: checks the note type, validates the field mapping, and asserts the target deck already exists — it creates **nothing**; `ensure_deck` is Deck-Builder-only), `get_existing_vocabulary`, `create_cards_batch` (batch size 100), `delete_notes`. Stores `last_created_note_ids` for undo support and `anki_write_state` (`AnkiWriteState`) as the run's write provenance. Three collaborators are split out so they are testable without HTTP mocks: `services/_ankiconnect.py` (the shared `post_action` transport — a deliberately stable patch target for white-box tests), `services/anki_note_builder.py` (CardPayload → note dict field mapping), and `services/anki_media_store.py` (`AnkiMediaStore`: streaming `storeMediaFile` uploads chunked at 50 files or ~4 MB of base64, with per-file fallback on a failed `multi` POST, shared by card media and dictionary-bundled assets).
- **ValidationService**: checks AnkiConnect connectivity, ffmpeg presence, deck existence, and note type existence. Returns `ValidationResult` (never raises).
- **YouTubeFetcherService** (`services/youtube_fetcher.py`): wraps the `yt-dlp` subprocess. Three entry points: `probe_metadata(url) → VideoInfo` (fast, `--skip-download --dump-single-json --no-playlist`), `probe_playlist(url, limit) → PlaylistInfo` (`--skip-download --flat-playlist --dump-single-json --playlist-items 1:{limit+1}`, the extra entry lets callers detect over-cap playlists), and `fetch_video(url, video_id, workspace, sub_mode, progress_cb, cancel_event, *, fallback_allowed=False) → FetchedMedia` (`fallback_allowed` lets a `manual_only` fetch fall back to auto-captions only when the probe already certified them native — callers pass the probe's `has_auto_ja_subs`). Detects native vs translated auto-captions via `_has_native_auto_ja()`. Tracks the `Popen` handle so cancellation can kill the full process tree (yt-dlp → ffmpeg child) via `psutil`. Writes the (video, subtitle) pair into a caller-owned workspace directory.

**Optional services (created based on config flags):**

- **MultiFrequencyService** (`services/frequency/`): aggregates an ordered chain of SQLite-indexed frequency sources built by `FrequencySourceRegistry` from `config.frequency_chain`. Each source is an `IndexedFreqProvider` over `freqs_root/<source_id>/index.sqlite`. `lookup_all(word)` returns the per-source breakdown for the card; the best (lowest) rank for filtering/sorting derives from the module-level `min_rank(lookup_all(word))` (harmonic sort field via `harmonic_rank`). The chain is user-populated via source import; `legacy_migration.repair_legacy_frequency_source_name` only fixes the `legacy-frequency` source's display name on startup (the old single-CSV `FrequencyService` was removed). Registry I/O happens in `load()`, not `__init__`.
- **MultiPitchAccentService** (`services/pitch_accent/`): FIRST-HIT-WINS aggregator over an ordered chain of pitch accent sources built by `PitchSourceRegistry` from `config.pitch_chain`. Each source is an `IndexedPitchProvider` over `pitch_root/<source_id>/index.sqlite` — the index is read fully into memory on `load()` and the connection closed (the SQLite file exists for the shared recovery substrate, not per-lookup queries). Unlike the additive frequency chain, the first enabled source whose three-tier `lookup_entry` resolves wins; later sources only fill words earlier sources miss. The legacy single `pitch_accent.csv` is folded into a `legacy-pitch` source by a one-time boot migration (`legacy_migration.migrate_legacy_pitch_csv`).
- **ASR transcription** (`services/asr/`): offline speech-to-text via `faster-whisper`. `transcriber.py` runs the model, `model_manager.py` handles in-app model/acceleration-pack downloads, `_engine.py` probes availability (degrades gracefully when the `[asr]` extra is absent). Feeds the Tools → Generate tab.
- **Subtitle retiming** (`services/subtitle_retimer.py`): the module-level `retime_subtitle()` function (with the `_run_alass` helper) realigns an out-of-sync subtitle file to a video via the external `alass` binary, resolved by `utils/alass_resolver.py` and installed in-app by `services/alass_installer.py`. Feeds the Tools → Retime tab.
- **Audio condensing** (`services/audio_condenser.py`): `AudioCondenserService` builds dialogue-only condensed audio from a media file plus its subtitles — computing kept intervals (padding, gap-merge, offset, line filtering) with pure interval math, then extracting/concatenating them and re-encoding to `mp3`/`opus`/`flac` via ffmpeg, optionally emitting a re-timed `.srt`+`.lrc`. Reads external `.ass`/`.ssa`/`.srt`/`.vtt` or an embedded text track (`ffprobe`-listed, decoded with a `charset-normalizer` fallback). Feeds the Tools → Condense tab.
- **Reading sources** (`services/reading/`): parses mokuro-processed manga volumes and Japanese books into `ReadingDocument`s. `detector.py` classifies a path (`.mokuro`/`.cbz` → manga, `.epub` → book, `.txt` → Aozora/plain; a `.cbz`/`.zip` resolves through its sibling `.mokuro` sidecar, else an embedded `.mokuro` member inside the archive — `ReadingSourceRef.ocr_entry`, Issue #103 — read via a capped in-memory zip-member read, never extraction) and `load()` dispatches to `mokuro_source.py`, `epub_source.py`, or `aozora_source.py`; `sentence_splitter.py` segments text and `images.py` materializes page/cover images. Anki Miner consumes mokuro's existing OCR — it does no OCR itself. DRM-protected EPUBs are rejected up front (`epub_source._check_encryption`).
- **KnownWordDB**: SQLite-backed persistent known word cache. Supports differential sync with Anki vocabulary.
- **WordListService**: loads blacklist/whitelist text files for word filtering.
- **StatsService**: SQLite-backed analytics (`mining_sessions`, `series_difficulty` tables). Provides aggregated stats and milestones.
- **UpdateChecker**: queries the GitHub Releases API for newer versions.
- **ExportService**: exports results to CSV, TSV, or vocabulary list formats.

**Dictionary providers** (`services/dictionary/providers/`):

- **IndexedDictProvider**: SQLite-backed offline provider used by every on-disk dictionary (JMdict and user-loaded Yomitan dicts). On first launch, JMdict XML is migrated to a SQLite index at `~/.anki_miner/dicts/jmdict-english/index.sqlite`; lookups run against that index. The read-only connection is opened with `check_same_thread=False` so a single instance is safe to share across worker threads.
- **DictionaryRegistry**: scans `config.dicts_root` (`ANKI_MINER_HOME/dicts/`) for installed dictionaries and builds the enabled provider chain from `config.dictionary_chain`. Disk I/O happens in the explicit `load()` call, not in `__init__`. Enabled providers remain in configured order; disabled entries are skipped.
- **JishoProvider**: opt-in REST client for the jisho.org API, disabled in the default `dictionary_chain`. Rate-limited with a configurable delay (`jisho_delay`).

**Expression audio** (`services/audio_packs/`, `services/expression_audio_fetcher.py`):

Word-level audio (Issue #73) runs through a `ChainedExpressionAudioFetcher` that walks an ordered list of `ExpressionAudioFetcher` implementations (the protocol is in `interfaces/expression_audio.py`; fetchers never raise) and returns the first non-None path. The chain is assembled by `service_factory` from `config.expression_audio_chain` — a list of `AudioSourceEntry` objects, each tagged `kind: "pack"|"jpod101"|"googletts"|"custom"|"custom_json"` with an enabled flag (`custom`/`custom_json` are URL-template sources — a plain audio URL or a local-audio-yomichan-style JSON endpoint). The default chain contains the JPod101 entry plus a disabled Google Translate entry, preserving pre-feature behavior with zero extra I/O for users who have not imported any packs.

Local audio packs are imported from [local-audio-yomichan](https://github.com/themoeway/local-audio-yomichan)-compatible directories. Five physical pack formats are detected: `ozk5` and `ajt` (both `index.json`-driven — `ozk5` is checked first), `nhk16` (entries.json + audio/, NHK 2016 accent survey), `forvo` (speaker subdirectories), and `jpod_legacy` (flat `{reading} - {expression}` stems, covers the JapanesePod101 archive and its alternate variant). Format detection is in `services/audio_packs/formats.py`; importing is in `services/audio_packs/importer.py`.

Each imported pack gets a SQLite index at `config.audio_packs_root/<pack_id>/index.sqlite` (`~/.anki_miner/audio_packs/` by default) with an `entries(expression, reading, source, speaker, display, file)` table and a meta table. The audio files themselves are never moved — each entry's `file` is a posix-style path relative to the pack directory, and the meta table stores the absolute `pack_dir` they resolve against. Import is atomic (stage to a temp location, then rename into place). `AudioPackRegistry` (`services/audio_packs/registry.py`) has an I/O-free `__init__` and a `load()` call; it is only instantiated when expression audio is enabled and at least one enabled pack entry is present in the chain.

`LocalAudioPackFetcher` opens a read-only SQLite connection per call, queries by `(expression, reading)` — using `mined_form` for expression to match the card's Expression field — and copies the matched file into `~/.anki_miner/audio_cache/local_packs/` as `{pack_id}_{mined_form}_{reading}{ext}`. An empty or whitespace-only reading skips the fetch entirely (mirroring `JPod101AudioFetcher`): a reading-less lookup falls back to wildcard row selection, which could cache the wrong homograph pronunciation permanently. It never returns in-place pack paths (containment guard prevents path traversal), so the cached copy is what gets stored in Anki. In a chained configuration, pack fetchers are inserted above the JPod101 entry (packs take priority); JPod101 still acts as a fallback for words not covered by any installed pack.

`GoogleTranslateAudioFetcher` (`services/google_translate_audio_fetcher.py`) is an optional synthetic-TTS source backed by the `gtts` library (free, no API key). It is fed the kana **reading**, not the kanji, so pronunciation is correct and immune to homograph misreads; an empty or whitespace-only reading skips the fetch (mirroring the other fetchers). It sits as a chain fallback, typically after JPod101 — JPod101 is recorded native audio, Google is synthetic — and is disabled by default, so default behavior is unchanged. Hits cache to `~/.anki_miner/audio_cache/googletts/` as `googletts_{mined_form}_{reading}.mp3`; there are no negative (`.miss`) markers because synthetic failures are transient and retried next run.

The gate is the field mapping, not a separate flag (there is no `expression_audio_enabled` config field): expression audio is written only when a fetcher is injected **and** `config.anki_fields["expression_audio"]` is non-empty. That field defaults to `""`, so the feature is off until the user maps it — the same activation pattern as frequency and pitch.

**Sentence TTS for reading sources** (`services/sentence_tts_fetcher.py`, protocol in `interfaces/sentence_audio.py`): reading-mined cards (manga/novels) have no source audio, so `process_reading` phase 3' can synthesize the card sentence instead. A `ChainedSentenceAudioFetcher` walks Google Translate TTS (shared gtts synthesis leaf with the word fetcher) then Naver Papago (unofficial two-step endpoint: POST `makeID` → GET the audio; the fetcher's never-raises contract is load-bearing since the scraped endpoint may drift). Assembled by `service_factory._build_sentence_audio_fetcher` from three plain config bools — `reading_tts_enabled` (master, off by default) + one per provider, fixed Google-first order. Gated by `EpisodeProcessor._reading_tts_active`; the video/YouTube/audiobook paths never consult it. Hits cache to `~/.anki_miner/audio_cache/sentence_tts/` as `sentencetts_{provider}_{sha1(sentence)[:16]}.mp3` (content-hash keys; no `.miss` markers), and one run synthesizes each unique sentence once, sharing the file across cards.

The import flow is in `gui/controllers/audio_pack_import_flow.py`; the panel it drives is `gui/widgets/panels/audio_pack_settings_panel.py`. Settings → Audio shows a row per installed pack with format, entry count, and a missing-folder badge when the original audio directory has moved. JPod101 and Google Translate appear as built-in rows that can be disabled but not removed. Pack rows can be reordered, disabled, or removed (deletes the index only; audio files are untouched). A right-click context menu offers re-import. The Add Audio Pack button opens a directory picker; if the chosen directory contains multiple detectable packs (e.g. a parent folder), all are imported sequentially and inserted above the JPod101 chain entry in the canonical pack-id insertion priority (nhk16 > shinmeikai8 > forvo > jpod > jpod_alternate, `audio_pack_import_flow._PACK_PRIORITY`) — a pack-id ordering, distinct from the physical-format list above.

## Orchestration

**EpisodeProcessor** (`orchestration/episode_processor.py`):
- Receives all services via constructor injection
- `process_episode(video_file, subtitle_file, progress_callback, curation_callback, cross_episode_counts, episode_name_override, series_name_override, audio_track_override, source_label_override, audio_only, cancel_event)` runs the 5-stage pipeline. `audio_only=True` is the Audiobook path (no per-word screenshots); `audio_track_override` pins a specific audio stream; `source_label_override` names the source on the card.
- `_run_pipeline(ctx, cancel_event, body)` is the shared run skeleton both entry points wrap: pre-flight gates (dictionary staleness, card-target verify, offline dictionary — all outside the `try` so a `SetupError` propagates instead of collapsing into a "completed" result), per-run temp allocation, the Anki accumulator reset, the `_external_cancel` bridge, and the try/except/finally tail. Path-specific work lives in the caller's `body` closure.
- `_stamp_write_provenance(result, failure=...)` is the single funnel every returned `ProcessingResult` passes through. It stamps `anki_write_state` from the live `AnkiService` (fail-closed to `NOTE_WRITE_UNCERTAIN` for anything that is not a real `AnkiWriteState`) and `failure_is_transient` from the raised exception — the two fields automatic retry consumes.
- `orchestration/audio_stage.py` (`AudioStage`) owns the expression-audio and sentence-TTS fetch loops and their progress-band accounting. It is the one cluster lifted out of the phase methods because it touches no pipeline ctx; `EpisodeProcessor` still constructs and closes the fetchers.
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

Phase 2 — build: `AnkiService.ensure_deck` creates the target deck if it does not exist (idempotent), and must run *before* the per-pair loop — `process_episode`'s pre-flight only asserts the deck exists. For each episode pair, a fresh `EpisodeProcessor` is created via `dataclasses.replace(config, anki_deck_name=deck_name, include_known_words=not collection_filter, bypass_optional_filters=True, allow_duplicate_cards=True)` — no production code other than the config fields changes. The last two are the load-bearing invariant: the per-episode reduction filters (i+1, frequency, word lists, sentence dedup/length) and Anki-side dedup must not run, or the build diverges from the preview the user approved — the "promised 2,401, built 51" failure. Known-words subtraction is the one filter that stays, gated on the collection checkbox. A `curation_callback` closure keeps a word only if its lemma is in the selected set and has not already been carded in a previous episode (`carded: set[str]` shared across the loop). This enforces the cross-episode "card each lemma once" invariant without touching `EpisodeProcessor` internals. `episode_name_override` and `series_name_override` are set to the video stem and deck name respectively so analytics rows are distinct from regular episode-mining sessions.

## Configuration

`AnkiMinerConfig` (`config/config.py`) is a frozen (immutable) dataclass with 102 fields. Grouped by area (not every field is listed):

- **Anki:** deck name, note type, field mappings, AnkiConnect URL
- **Media:** audio padding, screenshot offset, temp folder, subtitle offset (range ±300s), `ffmpeg_location` / `ffprobe_location` (explicit binary paths consumed by the resolver — see [ffmpeg / ffprobe](#ffmpeg--ffprobe))
- **Filtering:** min word length, allowed POS tags, excluded subtypes, deduplication, `exclude_hiragana_only_words` / `exclude_katakana_only_words` (kana-only drops, default off), `excluded_wordsets` (active bundled JMnedict name wordsets), `reading_min_occurrence` (per-volume minimum word occurrence for the Reading tab; 1 = off)
- **Dictionary:** `dictionary_chain` (the runtime-authoritative ordered list of providers — indexed dicts and Jisho, each toggleable), `dicts_root` (root for all installed `.sqlite` indexes; defaults to `ANKI_MINER_HOME/dicts/` via the `ANKI_MINER_HOME` constant in `config/paths.py`), Jisho URL/delay. Legacy `jmdict_path` is retained for the first-launch JMdict-XML migration only (`use_offline_dict` and the pre-v2.5 migration shims are gone; `gui_config.json` now carries a `config_schema_version` stamp).
- **Frequency:** `frequency_chain` (ordered tuple of `FreqEntry(source_id, enabled)` — the runtime-authoritative chain of frequency sources), `freqs_root` (root for the per-source `index.sqlite` files; defaults to `ANKI_MINER_HOME/freqs/`). The `frequency_sort` `anki_fields` entry writes the chosen sort value to its own card field.
- **Expression audio:** `expression_audio_chain` (ordered `AudioSourceEntry` list), `expression_audio_delay`, `audio_packs_root`
- **ASR (subtitle generation):** `asr_model`, `asr_device`, `asr_models_root`, `cuda_libs_root`, `onnx_pack_root`
- **YouTube:** `youtube_cookies_from_browser` (browser profile to pull cookies from) / `youtube_cookies_file` (explicit cookies file), max duration, subtitle mode
- **Appearance:** `theme`, `theme_favorites`, `themes_root`, `ui_font_scale` (whole-UI font scaling, clamped to [0.5, 2.0]), `ui_language`
- **Optional data:** pitch accent, frequency, known words DB, blacklist/whitelist paths and toggles
- **Analytics:** stats DB path
- **Performance:** max parallel workers (default 6)

The `__post_init__` method uses `object.__setattr__` to convert string paths to `Path` objects (required because the dataclass is frozen). New config instances are created with `dataclasses.replace()`.

**Config source:**
- GUI: `GUIConfigManager` (`gui/utils/config_manager.py`) persists to `~/.anki_miner/gui_config.json`. Defaults come from the `AnkiMinerConfig` dataclass field defaults.

## GUI Architecture

### Window Structure

`MainWindow` contains a `QTabWidget` with seven tabs (registered in `gui/app.py` as Video, Deck Builder, Audiobooks, Reading, Analytics, Utilities, Settings). Every container tab carries a stable `_subtab_index` dict mapping string keys to inner-tab indices and a duck-typed `open_subtab`, so `capabilities.SUBTAB_KEYS` can address a sub-tab by name and "Find a Feature" can reveal it (indices are never used as the identity).
1. **VideoTab** (`gui/widgets/video_tab.py`, "Video"): a container nesting three video-mining sub-tabs. **Single** (`SingleEpisodeTab`): file selectors (drag-and-drop), subtitle offset control, process button, log widget, progress widget. **Batch** (`BatchProcessingTab`): folder selection, `BatchQueue` management via queue panel, dual progress bars. **YouTube** (`YouTubeTab`, `gui/widgets/youtube_tab.py`): URL input + Add button, `QListWidget` queue of `YouTubeQueueItemWidget` rows (per-row status glyph, title, duration, sub source line, remove button), action buttons (Mine / Clear / Stop All), progress widget, log widget. Deck/note-type/tags widgets are global (see `AnkiSettingsPanel`). URL classification (plain video, playlist, video-in-playlist, Mix) is done without network access by `utils/youtube_url.py` (`classify_youtube_url`); playlist URLs dispatch to `YouTubePlaylistResolveWorker` then `YouTubePlaylistProbeWorker`; mixed watch+list URLs show a choice dialog; playlists over the `youtube_playlist_max` cap show an over-cap confirm. Each sub-tab keeps its own presenter and worker lifecycle; the container fans out config/shutdown and exposes the children's live workers via `iter_close_workers`.
2. **Deck Builder**: whole-series deck mining over a corpus of subtitles, driven by `DeckBuilderWorker` (see Orchestration). Two phases (aggregate/select, then build) separated by a GUI confirm gate.
3. **AudiobookTab** (`gui/widgets/audiobook_tab.py`, "Audiobooks"): audio + subtitle file selectors (subtitle auto-filled from a same-stem `.srt`/`.vtt`/`.ass`/`.ssa` next to the audio file) + Add button, `QListWidget` queue of `AudiobookQueueItemWidget` rows, action buttons (Mine / Clear / Stop All), progress widget, log widget. No probe stage — local pairs enter the queue READY. Mining runs `process_episode` with `audio_only=True`: no per-word screenshots; embedded cover art is extracted once per book and shared as every card's Picture (blank if absent), and the keep/drop decision keys on audio clip success. Stats identity: `series_name_override="Audiobook"`, `episode_name_override=<audio file stem>`.
4. **ReadingTab** (`gui/widgets/reading_tab.py`): a container nesting four mining sub-tabs. **Manga** (`ReadingMangaTab`) mines mokuro-processed manga volumes (the OCR is mokuro's, none is done here) through two cards mirroring the Novels tab: a Volume card picking a single `.mokuro`/`.cbz`/`.zip` file (sibling `.mokuro` sidecar or an embedded `.mokuro` member — no extraction either way) and a Manga Folder card whose pick expands a series folder into per-volume items. **Novels** (`ReadingNovelsTab`) mines a single `.epub` or Aozora/plain `.txt` book, or a whole folder of them (`detector.detect_book_folder`: top-level scan, non-recursive, one sequential queue item per book). **Subtitles** (`ReadingSubtitlesTab`, `gui/widgets/reading_subtitles_tab.py`) mines standalone subtitle files (`.srt`/`.ass`/`.ssa`/`.vtt`, several at once) with no video; rows are removable mid-run (skip routes through `ReadingQueueWorker.skip_item`). **Text** (`ReadingTextTab`, `gui/widgets/reading_text_tab.py`) mines pasted text: the tab builds a pathless `ReadingSourceRef(kind="text")` itself, so this is the one reading source that never goes through `detector`. All four subclass `_ReadingMiningTabBase` → `_QueueMiningTabBase` → `MiningTabBase`; mining runs `EpisodeProcessor.process_reading` (see Orchestration).
5. **AnalyticsTab**: mining statistics dashboard (queries `StatsService`).
6. **SubtitlesTab** (`gui/widgets/subtitles_tab.py`, "Utilities"): a container with four inner tabs. **Generate** (`SubtitleCreationTab`) transcribes a video/audio file into an SRT with a local Whisper model (`services/asr/`), with in-app model + GPU/VAD pack downloads and a CPU-fallback device selector. **Retime** (`SubtitleRetimeTab`) realigns an out-of-sync subtitle file to a video via `alass` (`services/subtitle_retimer.py`). **Condense** (`CondenseTab`, `gui/widgets/condense_tab.py`) builds dialogue-only condensed audio from a media file + subtitles via `AudioCondenserService` (`services/audio_condenser.py`), with a single-file mode and a batch folder mode; the embedded-track picker is `SubtitleTracksDialog` (`gui/widgets/dialogs/subtitle_tracks_dialog.py`). **Update Notes** (`CardBackfillTab`) bulk-fills fields on already-mined cards from newly installed resources (see `services/card_backfiller.py`).
7. **SettingsTab** (`gui/widgets/settings_tab.py`): a grouped nav list driving a `QStackedWidget` — 5 groups, 10 destinations. **Cards** (Cards & Anki, Card Media), **Resources** (Dictionaries, Word Audio, Frequency, Pitch Accent), **Mining** (Mining Rules), **Integrations** (YouTube, Transcription & Alignment), **App** (Appearance & Language). Each destination has a stable key (`anki`, `media`, `dictionaries`, `audio`, `frequency`, `pitch`, `filtering`, `youtube`, `subtitles`, `ui`) used by `reveal_setting` and the capability browser; `gui/widgets/settings_search.py` indexes the anchors registered by `gui/widgets/base/setting_anchor.py` (built after the Qt translators install, so the index is localized) and jumps to a result, showing the destination breadcrumb under it — it is a jump aid only, never a filter. Panels live in `gui/widgets/panels/`. Emits `config_changed`; `MainWindow` stamps and saves, then fans the committed object back out via `config_refreshed` so a stale worker snapshot cannot regain authority.

### Worker Threads

`CancellableWorker` (`gui/workers/base_worker.py`, QThread + `threading.Event`) provides:
- Thread-safe cancellation via `cancel()` / `is_cancelled()` / `check_cancelled()`
- Qt signals for results, errors, and progress

Two intermediate bases sit on it, both in `base_worker.py`:
- `ProcessorOwningWorker`: for workers driving an `EpisodeProcessor`. Declares the typed `curation_processor` contract (so GUI readers can't silently `getattr`-miss it) and the exactly-one-of `processor`/`processor_factory` constructor check.
- `SingleCallWorker`: one blocking call, one `result_ready` emission — the shape behind the short-lived AnkiConnect fetch workers.

`_queue_worker_base.py` owns the spine of the three sequential mining queue workers (YouTube, Reading, Audiobook): frozen item snapshot, identical four-signal shape, staleness pre-loop gate, deferred factory build, retry backoff. `file_queue_worker.py` does the same for the file-processing tools (subtitle generate / retime / condense), which share a byte-identical 5-signal contract.

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
- `ImportWorker` (`gui/workers/import_worker.py`): the unified `CancellableWorker` for dictionary, frequency, pitch accent, and audio-pack imports — per-domain `for_*` factories build the `(id, meta)` runners; user cancel routes to a distinct `cancelled` signal (not `failed`).
- `InstallWorker` (`gui/workers/install_worker.py`): the unified download/install worker behind the in-app installers (ASR models, CUDA/ONNX packs, external binaries), one task descriptor per tool; progress templates translate under each tool's own i18n context.
- `SubtitleGenWorker` (`gui/workers/subtitle_gen_worker.py`): `CancellableWorker` that runs the local Whisper (`faster-whisper`) transcription off the GUI thread for the Tools → Generate tab.
- `SubtitleRetimeWorker` (`gui/workers/subtitle_retime_worker.py`): `CancellableWorker` that runs the `alass` retiming subprocess for the Tools → Retime tab.
- `CondenseWorker` (`gui/workers/condense_worker.py`): `CancellableWorker` that drives a list of `CondenseItem` through `AudioCondenserService` sequentially (single file or batch folder) off the GUI thread for the Tools → Condense tab.
- `ReadingQueueWorker` (`gui/workers/reading_queue_worker.py`): `CancellableWorker` (owns its `EpisodeProcessor`) that drives a list of `ReadingQueueItem` through `process_reading` sequentially for the Reading tab.
- `DeckBuilderWorker` (`gui/workers/deck_builder_worker.py`): see Orchestration section.
- `BackfillScanWorker` / `BackfillApplyWorker` (`gui/workers/backfill_worker.py`): the read-only preview pass and the write pass of the Utilities → Update Notes tool (`services/card_backfiller.py`).
- `RestyleCardsWorker` (`gui/workers/restyle_cards_worker.py`): Tools → Restyle Mined Cards, driven through `background_tasks.start_restyle_cards`.
- `ResourceDownloadWorker` (`gui/workers/resource_download_worker.py`): downloads each `ResourceSpec` and routes it to the matching importer; per-item `try/except` so one failure never aborts the batch.
- `YtdlpUpdateWorker` (`gui/workers/ytdlp_update_worker.py`): drives `YtdlpUpdater` (auto-download / self-update of the managed yt-dlp binary).
- `PrewarmWorker` (`gui/workers/prewarm_worker.py`): best-effort background warming of `fugashi.Tagger()` and the dictionary indexes right after the main window first paints, so the first Mine click does not build them on the GUI thread.
- `FetchDecksWorker` / `FetchNotetypesWorker` / `FetchFieldsWorker` (`gui/workers/fetch_workers.py`): factories returning a `SingleCallWorker` around one `AnkiService` getter each, keeping the GUI responsive during AnkiConnect HTTP.

Beyond QThreads, `gui/utils/run_off_thread.py` provides `run_off_thread` for one-off blocking work in a GUI slot (worker ownership is automatic so it cannot be GC'd mid-run) plus `still_running` / `join_or_retain` for deleted-wrapper-safe bounded joins. Offloading is an **enforced convention, not an option**: `gui/utils/stall_watchdog.py` runs by default in the shipped app (250 ms heartbeat QTimer + daemon monitor thread) and logs a WARNING with the GUI thread's stack trace whenever the event loop goes stale, so a re-introduced blocking slot surfaces in the log instead of as an unexplained freeze.

### Signal Architecture

`GUIPresenter` emits Qt signals from worker threads. Main window slots receive them on the GUI thread. Per-tab presenters avoid cross-tab signal pollution. `GUIProgressCallback` bridges the `ProgressCallback` protocol to Qt signals.

GUIPresenter does **not** explicitly inherit from `PresenterProtocol`. It satisfies the protocol via structural subtyping, which avoids a metaclass conflict between `QObject` and `Protocol`.

### Theme System

Theme singleton backed by JSON theme files in `gui/resources/styles/themes/`. 29 built-in themes: 9 named families (Ayu, Catppuccin, Dracula, Everforest, GitHub, Gruvbox, Kanagawa, Rosé Pine, Solarized) plus 6 standalone themes (Dark, Light, Nord, One Dark, Sakura, Tokyo Night) that carry no `family` field. The `discover_themes()` function scans the themes directory at startup, validates each JSON file against a required color key schema (`REQUIRED_COLOR_KEYS`), and registers valid themes. A single `common.qss` stylesheet uses `${color-*}` variable substitution. The `Theme._substitute_variables()` method merges layout variables from `_variables.py` with color variables extracted from the active theme JSON. Custom themes can be added by dropping a valid JSON file into the themes directory. Theme preference is saved via `QSettings`.

### Video Preview (embedded libmpv)

The in-app video preview runs on libmpv via the `python-mpv` binding (not Qt Multimedia — the Qt FFmpeg backend had no software AV1 decode and froze on Windows sink teardown). Three layers:

- `utils/mpv_loader.py`: the ONLY module allowed to `import mpv`. Resolves libmpv in order env override (`ANKI_MINER_LIBMPV`, fails closed) → PyInstaller-bundled library at the `sys._MEIPASS` root → system library, monkeypatching `ctypes.util.find_library` around the import because python-mpv dlopens at import time. Also owns the C-numeric-locale assertion (`_ensure_c_numeric` before every `mpv.MPV(...)` construction — Qt stomps `LC_NUMERIC`), the `create_mpv_player` factory (software decode, `hwdec=no`), `mpv_available()`, and `mpv_probe_main()` — the display-free bundle probe `bundle_smoke.sh` drives via an env-gated hook in `gui/app.py`.
- `MpvVideoWidget` (`gui/widgets/mpv_video_widget.py`): a `QOpenGLWidget` view on the libmpv render API (`render_gl.h`; works on Wayland where `wid` embedding cannot). A dumb view — owns only the render context, which MUST be freed (`detach`) before the owning `MPV` handle terminates or libmpv aborts the process.
- `SubtitlePlayerWidget` (`gui/widgets/subtitle_player_widget.py`): the controller. Owns the `mpv.MPV` handle (one per widget lifetime; re-sourcing uses `loadfile`), holds all playback policy, and bridges python-mpv's event-thread callbacks to the GUI thread via queued Qt signals (every slot None-guards — a None property value is the normal first event).

Release bundles ship libmpv from the repo-owned `vendor-libmpv-*` GitHub releases (see RELEASING.md); pip/`.deb`/source installs use the system libmpv, and the preview pane shows a notice when none is found.

### Dialogs

- `WordCurationDialog`: user selects which discovered words to mine (cross-thread via a `threading.Event` bridge). Embeds `SubtitlePlayerWidget` per row for in-place audio/video preview, and renders multi-dictionary lookup via `DefinitionService.lookup_all_offline`.
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

GET `https://jisho.org/api/v1/search/words?keyword=<word>`. Rate-limited with configurable delay (default 0.5s). Surfaced as `JishoProvider` inside the configurable provider chain — its position is user-controlled via `config.dictionary_chain`. It is disabled by default; when enabled in the default position, it sits after `IndexedDictProvider(jmdict-english)` as the online fallback. Users may move it ahead of any indexed dictionary.

### ffmpeg / ffprobe

- **ffmpeg:** `-ss` seek + `-i` input + `-frames:v 1` for screenshots, `libmp3lame` (mp3) or `libopus` (opus) for audio extraction per `config.audio_format`
- **ffprobe:** `-show_streams -select_streams a` for Japanese audio track detection
- Parallel execution via `ThreadPoolExecutor` (default 6 workers)

**Binary resolution.** All ffmpeg/ffprobe invocations (media extraction, subtitle-preview probe, YouTube fetch) go through a resolver instead of assuming a bare `ffmpeg` on PATH. Resolution order: explicit `config.ffmpeg_location` / `config.ffprobe_location` → bundled binaries shipped inside the frozen app → PATH. The standalone builds (Windows Inno `Setup.exe`, macOS `.tar.gz`, Linux AppImage) bundle GPL ffmpeg + ffprobe fetched per-OS by the release workflow, with encoder presence (`libmp3lame`, `libopus`, `libsvtav1`, `libwebp`, `libwebp_anim`) asserted in the release smoke step. The Linux `.deb` deliberately omits the bundled binaries (GPL distribution constraints) and resolves to the system ffmpeg; PyPI/`pipx` and source installs likewise rely on PATH. A startup health check validates the resolved ffmpeg/ffprobe and surfaces a clear error if neither bundled nor PATH binaries are usable.

### yt-dlp

Subprocess invoked by `YouTubeFetcherService`. Single-video probe uses `--skip-download --dump-single-json --no-playlist`; playlist probe uses `--flat-playlist --dump-single-json` with `--playlist-items 1:limit+1` (the extra entry lets callers detect over-cap playlists); fetch adds `--sub-lang ja --sub-format vtt/best --convert-subs srt` + a height-capped format string, plus the subtitle flags: `auto_only` passes `--write-auto-sub`; `manual_only` passes `--write-sub` **and additionally** `--write-auto-sub` when `fallback_allowed`. That is one invocation writing one file — yt-dlp's own `process_subtitles` loads manual tracks first and only lets `automatic_captions` fill languages not already present, so both flags together mean "manual preferred, auto as fallback". The auto flag is gated rather than passed unconditionally because for a non-Japanese-audio video `automatic_captions["ja"]` is a machine translation; ungated, a `manual_only` video whose manual track vanished between probe and fetch would silently mine translated Japanese. Progress is parsed from a custom `--progress-template`; post-download phases are detected by scanning for the `_POSTPROCESS_MARKERS` line signatures (`[Merger]`, `[FixupM3u8]`, `[SubtitleConvertor]`, `[ExtractAudio]`). Process tree killed via `psutil` on cancel (yt-dlp spawns ffmpeg as a child for merging; `Popen.terminate()` alone leaks it on Windows). Optional `--cookies-from-browser` (from `config.youtube_cookies_from_browser`) or `--cookies` file (from `config.youtube_cookies_file`) enables bypass of bot-detection prompts and age-restricted content.

### Bundling yt-dlp

The standalone **binary** is vendored, not the Python package. Every call site spawns yt-dlp as a subprocess, so the importable `yt_dlp` module was never used at runtime; `anki_miner.spec` excludes it. It stays a pip dependency, which is how non-frozen installs get the console script that the resolver's interpreter-sibling tier finds.

`.github/ytdlp-pin.json` is the single source of truth for the pinned version and per-OS SHA-256 digests, read by both `.github/workflows/release.yml` and `scripts/release_preflight.sh`. Both fetch into `vendor/yt-dlp/`, which the spec bundles at `sys._MEIPASS/bin/` — the tier `ytdlp_resolver.py` checks after PATH. Assets must be standalone builds (`yt-dlp_linux`, `yt-dlp.exe`, `yt-dlp_macos`, the last being universal2 so one asset serves both macOS legs); the bare `yt-dlp` asset is a zipapp that shebangs the system `python3` and carries no `curl_cffi`.

`scripts/check_ytdlp_pin.py` gates freshness, since yt-dlp ships roughly monthly and dependabot cannot see a curl'd release-asset URL. It fails on a definitively stale pin or a zipapp asset, and only *warns* when the GitHub API is unreachable or rate-limited, so a transient API hiccup cannot block a release. The bundled smoke (`ANKI_MINER_SMOKE=youtube` in `anki_miner/gui/app.py`) runs `--version` and `--list-impersonate-targets` against the absolute bundled path rather than through the resolver, which prefers PATH and would otherwise resolve a developer's own binary.

### libmpv (video preview)

Loaded in-process through the `python-mpv` binding — see [Video Preview (embedded libmpv)](#video-preview-embedded-libmpv) for the loader/view/controller split. Distribution mirrors ffmpeg's: the standalone builds (Windows `Setup.exe`, macOS `.tar.gz`, Linux AppImage) bundle a libmpv shared library fetched by pinned URL + SHA256 from the repo-owned `vendor-libmpv-*` GitHub releases (built by `.github/workflows/vendor-libmpv.yml`; GPL bookkeeping in `licenses/libmpv/`), while the `.deb`, PyPI/`pipx`, and source installs resolve the system libmpv. Absence is non-fatal — `mpv_available()` gates the preview UI and a notice replaces the pane.

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
    ├── YtdlpNotFoundError
    └── NoJapaneseSubtitlesError
```

Defined across `exceptions/` (`base.py`, `validation.py`, `anki.py`, `media.py`, `subtitle.py`, `youtube.py`). `FfmpegNotFoundError` is a direct subclass of `AnkiMinerException`, not of `YouTubeFetchError`, even though only the YouTube fetch path raises it today.

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
| `audio_cache/sentence_tts/` | Files | Reading-path sentence TTS: `sentencetts_{provider}_{sha1(sentence)[:16]}.mp3` (content-hash keys, no `.miss` markers) |
| `runtime_state/downloads/` | Files | Partial-download resume state: `<key>.part` bodies + `<key>.json` manifests (`services/download_resume.py`) |
| `runtime_state/queues/` | JSON | Queue-contents snapshots written on close, one file per queue (`gui/utils/queue_state_store.py`) |
| `asr_models/` | Files | Downloaded local Whisper models (Tools → Generate) |
| `cuda_libs/` | Files | In-app CUDA acceleration pack for ASR |
| `onnx_pack/` | Files | In-app ONNX/VAD pack for ASR |
| `bin/` | Files | In-app-installed external binaries (`alass`, the managed yt-dlp binary + its verification receipt) |
| `.ytdlp_update_check` | Text | Unix timestamp of the last yt-dlp update check; throttles the next one (`services/ytdlp_updater.py`) |
| `themes/` | JSON | User-added custom theme files |
| `anki_miner.log` | Log | Application log |

Temporary media files are stored in the system temp directory under `anki_miner_temp/` and cleaned up after each processing run. YouTube downloads go one level deeper — `anki_miner_temp/youtube/run-<uuid>/` — owned by `YouTubeQueueWorker` (one workspace per attempt; cleaned up in `finally` on every exit path) and `rmtree`'d on every exit path (success, cancel, exception). Reading (manga/novel) mining materializes each word's page or cover image into a temp workspace rather than running ffmpeg.
