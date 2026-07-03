# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Word audio can come from any URL or a local audio server.** New audio source kinds in Settings → Audio: a custom URL template ({term}/{reading} substitution) and custom-JSON — the same integration contract Yomitan uses for the local-audio-yomichan server — plus optional (off-by-default) JPod101-dictionary and Jisho scrape sources with reading validation. All free/keyless; sources chain in priority order as before.
- **Occurrence-count frequency lists import correctly.** Yomitan zips declaring `occurrence-based` mode were rejected, and occurrence-count CSVs silently inverted the Max-frequency-rank filter; both are now auto-converted to ranks at import (declared mode honored; undeclared CSVs are probed with Yomitan's common-vs-rare word heuristic), with the conversion noted in the import dialog.
- **Cloze, conjugation, and word-audio-friendly split fields (opt-in).** New mappable card fields: ClozePrefix/ClozeBody/ClozeBodyKana/ClozeSuffix split the sentence around the target word for cloze-style note types (with the body's kana aligned to the inflected form, following Yomitan), and a Conjugation field records the word's inflection chain (e.g. -て « -いる « -た). Unmapped fields stay off; nothing changes by default.
- **Pitch accent can render as a graph and an overline (opt-in).** Two new mappable card fields draw the pitch contour instead of the bare `0,2` number: PitchGraph is the OJAD-style dot-and-line graph (downstep ring, dashed tail to the following particle) and PitchText is the reading with a per-mora overline and downstep tick, plus red rings for devoiced/nasal mora when the pitch data carries them (following Yomitan's pronunciation renderer). Both are self-contained inline SVG/HTML using `currentColor`, so they render on Anki desktop and mobile with no note-type CSS and track the card's text color in dark mode. Unmapped by default; nothing changes unless you map them.
- **Sentences can be trimmed to sentence boundaries (opt-in).** With `trim_to_sentence` enabled, multi-sentence subtitle cues are cut down to just the sentence containing the target word, using Yomitan's sentence-boundary walker (terminator + quote pairing). Off by default.
- **Dictionary imports validate banks and report skipped entries.** Malformed term/meta entries are counted and surfaced in the import dialog instead of vanishing silently; a dictionary zipped one folder too deep now gets a precise error naming the redundant directory (the classic re-zip mistake), for definition, frequency, and pitch zips alike.
- **Word audio "not found" markers now heal themselves.** JPod101 negative-cache markers expire after 180 days, so words JPod101 adds later are eventually retried automatically — and a new Settings → Audio → "Retry missing word audio" button clears them on demand (no more deleting the cache folder by hand).
- **Kana-only words can be mined (opt-in).** With the new `mine_kana_only_words` setting enabled, pure-hiragana words that an installed offline dictionary attests (ごまかす, しゃべる, うなずく) are mined instead of silently skipped; following Yomitan, the dictionary — not the script — decides wordhood. Off by default; requires an enabled offline dictionary.
- **Dictionary tags render as labeled chips.** A dictionary's tag metadata (its `tag_bank` / legacy `index.json` tagMeta — parts of speech, usage notes like "usually written using kana alone", popularity) is now imported and shown as hover-labeled chips on the card instead of a cryptic comma list; the note text appears on hover. Tags a dictionary doesn't define keep the old inline text. Re-import a dictionary to pick up its tags.
- **Anki Miner offers to re-import dictionaries after a format upgrade.** When a dictionary's on-disk index predates a schema change, mining used to silently produce zero cards (every word dropped for lack of a definition). Now the app detects the stale index on startup and offers one-click "Reimport All", and every mining run (single, batch, YouTube, audiobook, Deck Builder) refuses to start with an actionable message — "…needs reimport (schema upgrade) — Settings → Dictionaries → Reimport All" — until you do, aborting the whole run once instead of failing item by item. Frequency indexes are unaffected: older ones keep working and only miss the newest display values.

### Changed
- **Definitions lead with the sense that matches the sentence's reading, and unrelated senses are grouped separately.** A homograph like 辛い(からい/つらい) or 打つ(うつ/ぶつ) now sorts the sense matching the reading it had in the sentence to the front of the card's definition, following Yomitan's reading-match ranking — a boost, not a filter, so the other reading's senses still appear below (nothing is dropped on a misread). Senses that belong to different dictionary entries but happen to share a reading (橋/箸) now render as separate blocks with their own part-of-speech tags instead of being merged under one tag line, and duplicate senses are collapsed before the display cap so a double-keyed entry can no longer crowd out a real sense. Re-mine to refresh existing cards.
- **Duplicate scope is configurable, and media clips no longer collide across episodes.** New Duplicate Handling settings choose whether duplicates are checked per-deck or per-collection (with Anki's deck-root synthesis, following Yomitan); media filenames now carry a short content hash, so two different clips mined for the same word at the same timestamp in different files no longer overwrite each other in Anki's media folder.
- **Duplicates are detected before submitting, and real errors stop masquerading as duplicates.** Following Yomitan, each batch is pre-checked with AnkiConnect's `canAddNotesWithErrorDetail` (first-field-only probes), so duplicate counts are exact and a genuinely broken note (bad field mapping, empty first field) now surfaces as an error instead of being silently skipped as a "duplicate". Older AnkiConnect versions fall back to a two-pass `canAddNotes` diff. Deck Builder re-runs against an already-built deck now skip existing cards instead of re-adding them (fresh builds unchanged).
- **Frequency data is reading-aware and shows the author's display values.** Homographs (方 かた/ほう) now look up frequency under their in-sentence reading instead of inheriting the best rank of any reading — affecting the displayed rank and Max-frequency-rank filtering — and frequency dictionaries that publish display strings (JPDB-style "1099/72000", ㋕ markers) now render those on the card instead of being skipped at import. Existing frequency indexes keep working unchanged (re-import a source to gain its display values).
- **Dictionary images render correctly on dark note types.** Monochrome dictionary SVGs (accent diagrams and similar black-stroke art) now recolor to the card's text color via Yomitan's mask/currentColor technique, and pixel-art images render crisply (`pixelated`). Applies to newly imported/reimported dictionaries.
- **Anki Miner no longer writes to your note type's card styling.** Glossary CSS is embedded inside each card (a scoped `<style>` block in the glossary field), so the base stylesheet ships on every glossary-bearing card — minified, so notes stay reasonably small. If a previous version left an "ANKI MINER DICT STYLES" block in your note type, the per-card styles take precedence so cards still look correct; you can remove that block from the note type's Cards → Styling screen. Custom CSS (Settings → Card Styling) is appended inside each card's block.
- **Frequency sort column now uses a harmonic mean and sorts unranked words last.** The FrequencySort card field carries the harmonic mean of a word's per-source ranks (following Yomitan) instead of the single best rank, so one niche frequency list can no longer dominate the sort. Words that no source ranks now write a 9999999 sentinel, so they sort *after* rank 1 in Anki's browser rather than before it (an unranked word previously left the field empty, which sorts first). The frequency *filter* (Max frequency rank) still uses the best rank in any source and is unchanged. Only affects users who mapped the FrequencySort field; re-mine to refresh existing cards.
- **Subtitle text is normalized before tokenization.** Halfwidth katakana (ﾊﾟｿｺﾝ → パソコン), decomposed (NFD) kana, CJK-compatibility ligatures (㍿ → 株式会社), Kangxi-radical substitutions (⼭ → 山), and the 𠮟 variant now normalize before MeCab sees the line — words that previously tokenized as unknowns and were silently skipped now mine, and the stored card sentence shows the normalized text. Kanji detection also covers extended ranges (Ext-A, compatibility, astral), so words written with those characters are no longer rejected.
- **Furigana is distributed per kanji.** Readings now sit over the individual kanji they belong to instead of a single bracket spanning the whole word: 入り口 renders as 入[い]り 口[ぐち] (rendaku handled), 取り引き as 取[と]り 引[ひ]き, and 落ち着く as 落[お]ち 着[つ]く, following Yomitan's furigana distribution. Genuinely ambiguous words (e.g. 飼い犬) keep one whole-word reading rather than guess. Affects the ExpressionFurigana and SentenceFurigana card fields; re-mine to refresh existing cards. Display only — no cache, dedup, or audio identity depends on furigana.

### Fixed
- **Mined-card glossary styling is fixed and self-contained again.** The v2.7.6 rework moved the dictionary CSS out of each card into one stylesheet written to your note type; when that wasn't in place, mined cards — including Jitendex/Lapis glossaries — rendered bare (part-of-speech tags ran together, sense and example-sentence styling gone). The CSS now travels *inside* each card again (a scoped `<style>` block in the glossary field), the way Yomitan delivers it, so cards style correctly on any note type, on AnkiDroid/mobile, and when exported or shared — with nothing to sync, strip, or de-sync.
- **Context-dependent noun readings match the sentence.** A word whose kanji reads more than one way (方 かた/ほう, 中 なか/ちゅう) now keeps the reading it had in the mined sentence — on the card's ExpressionReading and ExpressionFurigana, in its pitch-accent lookup, and in its word audio — instead of a reading picked by re-analyzing the word on its own. Following Yomitan, the reading flows from the single in-context analysis rather than being recomputed in isolation. The first run after upgrading re-fetches word audio once for affected nouns (the old cache entries simply go unused); verb and adjective readings are unchanged.
- **Accented verbs and adjectives are now categorized 起伏 (kifuku).** Following the standard NHK convention (as Yomitan does), any downstep on a verb or i-adjective is 起伏, not 中高/頭高/尾高 — so 食べる[2] is kifuku rather than the mislabeled 中高. Previously only a drop on the final mora counted, which almost never happens for verbs, leaving nearly every accented verb/adjective mislabeled. The PitchCategory card field changes accordingly for verbs and adjectives; re-mine to refresh existing cards.
- **Pitch accent matches the word's reading.** A homograph (弾く ひく[0] vs はじく[2]) now gets the pitch pattern for the reading it had in the sentence instead of whichever reading happened to load first, and importing a Yomitan pitch dictionary now keeps its `[HL]`-string patterns and per-mora nasal/devoice annotations (previously silently dropped). Following Yomitan, pitch lookup is strictly reading-scoped. Existing 3-column `pitch_accent.csv` files keep working unchanged; re-import the original pitch zip once to gain the nasal/devoice/H-L enrichment.

### Removed


## [2.7.7] - 2026-07-02

> Supersedes 2.7.6, which reached PyPI but shipped no downloadable app build; its changes are folded in below.

### Added
- **Vulkan GPU transcription.** Subtitles → Generate can run the local Whisper model through a whisper.cpp Vulkan backend, so AMD and Intel GPUs get hardware-accelerated transcription alongside NVIDIA CUDA. The device selector gains a "Vulkan" option, and Download Vulkan model fetches the ggml model plus the Silero voice-activity pack. Vulkan ships only in the downloadable app builds; the PyPI install stays CPU-only, and a Vulkan device falls back to auto (CUDA if present, otherwise CPU) when no Vulkan backend is available.
- **Whole words and expressions instead of fragments (dictionary-attested compound matching).** 走り出した now mines one card 走り出す instead of 走り; 応急処置 stays one card instead of splitting into 応急 + 処置. Following Yomitan's approach — the dictionary defines word boundaries — the parser now merges adjacent tokens whenever the joined form (deinflected via UniDic orthBase) is a headword in an installed offline dictionary, longest match first. This includes expressions across particles: 気がした mines 気がする, and an attested collocation like 結論を出す replaces its component cards for that occurrence (by design — the longest dictionary-attested match wins; components stay mineable where they appear alone). Requires at least one enabled offline dictionary; without one, mining is unchanged. Toggle: `compound_matching` in the config file (default on). Words previously marked known as fragments may resurface as unknown compounds — that is the fix taking effect.
- **Seven new UI languages.** German, Brazilian Portuguese, Simplified Chinese, Traditional Chinese, Italian, Indonesian, and Vietnamese join the existing English, Japanese, Spanish, French, and Russian translations, selectable in Settings (restart to apply).

### Changed
- **Card glossary styling is now a single universal stylesheet.** A Yomitan-faithful stylesheet is always applied and combined with each dictionary's own scoped CSS and your custom CSS into one managed block, replacing the per-preset styling model. Styling syncs automatically on Save.
- **Subtitles panel reorganized.** Transcription downloads are grouped under a "Transcription add-ons (optional)" section with a one-line description under each, and component status now reads a consistent "Installed."

### Fixed
- **Cleaner, silence-free transcripts.** ASR now runs Whisper without its VAD filter for tighter segment boundaries, then drops hallucinated non-speech lines with an independent Silero speech mask. Because it transcribes the real timeline instead of concatenated speech, a short line no longer stretches across minutes of silence.
- **Windows GUI no longer freezes when closing the preview** (Win11, FFmpeg backend). The video sink is detached before the media player is destroyed, so teardown no longer hangs the GUI thread.
- **alass shows "Installed" when bundled or on PATH**, instead of only when downloaded into the managed folder — the status probe now resolves the full binary chain (override, bundled, managed, and PATH).
- **No more duplicate subtitle files on Windows.** Subtitle output resolves to the real on-disk filename, so Unicode-normalization (NFC/NFD) differences no longer spawn a second file.
- **Animated screenshots fall back to WebP when the AVIF encoder is missing.** On a build whose ffmpeg lacks the SVT-AV1 (`libsvtav1`) encoder — notably the Intel macOS bundle — mining with AVIF animated screenshots configured dropped every word (the screenshot failed, so the word was excluded) and surfaced only an opaque "No media extracted successfully". The format is now resolved once per run: AVIF falls back to WebP (`libwebp_anim`, which the Intel bundle ships), and the Activity Log states the fallback explicitly. If no animated encoder is available at all, the log says so and points at the static-screenshot setting, instead of failing silently.
- **Dictionary CSS scoper no longer drops rules when a comment contains `<`** (#89). A `<` inside a CSS comment in a rule's selector (e.g. Jitendex's forms-table rule, whose comment mentions `<div>`/`<li>`) tripped the `</style>`-breakout guard and silently dropped the whole rule, losing round form-priority icons (`clip-path`) and forms-table cell borders. The prelude is now checked comment-stripped (its comments are never emitted); the body check stays raw because the body is emitted verbatim, so a `</style>` in a body comment really would break out.
- **AV1 preview "can't decode" notice no longer false-fires on capable hardware** (#82, follow-up). The first-frame nudge now seeks to the first subtitle and `pause()`s — the same path word-click already uses — so a hardware AV1 decoder actually presents a frame before the watchdog checks. The watchdog window was also widened to allow a slow cold hardware-decoder init. Mining is unaffected as always — screenshots come from FFmpeg, not the preview.
- **Furigana keeps okurigana outside the ruby brackets** — 終[しま]い rather than 終い[しまい], so the reading sits over the kanji only.
- **Quick Processing updates both progress bars** (Overall and Current Episode) during folder runs.
- **Closing Settings while it is still querying Anki no longer raises an error.** The field, deck, and styling probe callbacks now no-op when the panel has already been destroyed.
- **Glossary sense items no longer inherit your note type's list-bullet styling.**
- **Glossary styling no longer triggers a needless Anki sync on every launch.** The universal stylesheet is reconciled at startup and on Save; it now writes the note type only when the CSS actually changes, so an unchanged note type is no longer marked modified — which previously forced an AnkiWeb sync every time the app opened.
- **Mined verbs and adjectives keep the kanji spelling the subtitle used.** A sentence containing 乞う previously produced a card whose Expression read 請う: unidic's canonical lemma silently swaps orthographic kanji variants (乞う→請う, 喰らう→食らう). The Expression now uses the dictionary form in the sentence's own orthography (UniDic orthBase) — the same behavior as Yomitan, which deinflects the source text and never normalizes the spelling. Definition, frequency, and pitch lookups still use the lemma, so card data is unchanged. A verb previously carded under the normalized spelling will be offered once more as the source spelling if re-encountered (no migration, same precedent as legacy surface-form cards). After an i+1 sentence swap the Expression keeps the first-seen spelling even if the swapped sentence uses another variant — unchanged in kind from the previous lemma behavior.
- **Sentence bold now covers the whole conjugated word.** 蒔いた is bolded as 蒔いた, not 蒔い — the highlight span is verified with a port of Yomitan's deinflection rules (auxiliary chains like 泳いでいた and 蒔いたら bold fully; where Yomitan has no rule, e.g. 買ってくれた, the span stops at 買って, matching Yomitan). Applies to both the Sentence and SentenceFurigana fields, including sentences swapped in by the i+1 filter.


## [2.7.5] - 2026-06-27

### Fixed
- **Intel macOS release build.** The Intel (x86_64) macOS binary was broken across the 2.7.x line by three platform-specific issues, now resolved: GitHub retired the `macos-13` runner (job queued forever) → moved to `macos-15-intel`; onnxruntime (a hard faster-whisper dependency) no longer ships macOS x86_64 wheels → the Intel build omits the `[asr]` extra; and the Intel ffmpeg build ships no SVT-AV1 encoder → the encoder smoke drops it on Intel. The Intel macOS binary ships again. On Intel macOS only, local Whisper ASR and AVIF animated screenshots are unavailable (WebP animated + static screenshots work; ASR remains available on Linux/Windows/macOS-arm64 and via `pip install anki-miner[asr]`). pip installs were unaffected throughout.


## [2.7.2] - 2026-06-27

### Added
- **Frequency Sort field mapping.** Settings → Anki adds a FreqSort field so the frequency-sort value can be written to its own card field (label and helpers translated into es/fr/ja/ru).

### Fixed
- **ASR binary builds.** The bundled `av` (PyAV) module was missing from release binaries, which broke the offline ASR smoke and failed the v2.7.1 binary release. PyAV is now bundled and binaries ship again. (pip installs were unaffected — pip resolves `av` as a normal dependency.)


## [2.7.0] - 2026-06-27

### Added
- **Subtitle generation from speech.** A new Subtitles → Generate tab transcribes a video or audio file into an SRT with a local Whisper model, so you can mine sources that ship without subtitles. The model downloads in-app; an optional GPU (CUDA) acceleration pack and a voice-activity-detection pack install the same way, and a device selector falls back to CPU when no GPU is available.
- **Subtitle retiming.** Subtitles → Retime realigns an out-of-sync subtitle file to your video using alass, with the alass binary installed in-app.
- **Multiple frequency sources.** Settings → Frequency now takes a chain of frequency lists instead of a single file, each stored in its own SQLite index, with a `frequency_sort` field for ordering. An existing `frequency.csv` is migrated into the chain once on first launch.
- **Find a Feature browser.** Tools → Find a Feature opens a searchable list of the app's capabilities and jumps you straight to the relevant tab or setting.
- **Spanish, French, and Russian UI.** Three new UI translations join the completed Japanese one, selectable in Settings (restart to apply). English stays the default; every catalog is drift-guarded in CI.
- **Whole-UI Zoom.** A Zoom setting scales the entire interface via `QT_SCALE_FACTOR` for high-DPI or low-vision setups.
- **JP Mining Note support.** Card-type marker fields let you map cards onto the JP Mining Note note type.
- **Per-dictionary card styling** (#87). Imported dictionaries now render with their own `styles.css`, with a generic structured-content fallback for dictionaries that ship none.
- **Intel macOS build.** Releases now include an `x86_64` macOS bundle for Intel Macs, and the update check points Intel users at the right asset.
- **Word list sorted by occurrence** (#88). The curation dialog orders words by how often they appear in the source.

### Changed
- **ASR folded into the Subtitles panel.** Speech-recognition settings and the alass, model, and acceleration-pack installs all live under one Subtitles panel.
- **Audiobook tab renamed Audio.** Audio mining has never been limited to audiobooks, and the wording now reflects that.
- **Card-style presets reworked into Rich and Minimal.** The preset list is now two clear, bug-free options.
- **Recommended frequency resources import into the chain** instead of a standalone file.

### Fixed
- **The GUI no longer freezes during long operations.** Blocking work — subtitle parsing, track and ffprobe probing, dictionary/frequency/audio registry scans, analytics, note-field checks, and processor construction — now runs off the GUI thread. A stall watchdog flags any remaining main-thread blocking, leaked mining processors are reaped after a stuck-worker timeout, and off-thread workers are joined at app close.
- **Retiming no longer breaks well-timed subtitles.** alass is only allowed to shift subtitles that are actually out of sync.
- **Subtitle generation is more reliable.** Whisper hallucinations are suppressed, audio is written as `pcm_s16le` so the standard library can read it back, model downloads are atomic and integrity-checked, the audio-decode path is hardened, the extraction timeout is raised for long sources, and blank transcripts are no longer reported as success.
- **Dictionary styling fixes** (#87). The muted-text opacity no longer cascades across presets, and a ReDoS in the `styles.css` forbidden-pattern scan was removed.
- **Sibling subtitles match case-insensitively** when auto-filling the subtitle selector from a picked video.

### Removed
- Dead internals: the legacy `FrequencyService`, the single-CSV frequency importer, and unused `_AsrState` fields.


## [2.6.7] - 2026-06-23

### Added
- **Sentence picker in the word curator.** When a word appears in more than one subtitle line, the curation dialog now lets you choose which sentence is mined onto the card instead of always taking the first occurrence.

### Changed
- **Words with no definition are dropped before the curation dialog.** An offline definition filter runs ahead of curation, so words that would land on a blank card no longer show up to curate.
- **Duplicate expressions within a run are collapsed before curation.** When the same expression appears more than once in a single run, it now shows up once in the curation dialog instead of repeating.

### Fixed
- **AV1 preview "can't decode" notice no longer false-fires.** The player nudges the first-frame decode so machines that can decode AV1 stop getting the fallback notice by mistake.
- **Sentence-picker preview seeks correctly and Space toggles playback reliably.** Selecting a candidate sentence jumps the preview to the right spot, and the Space key starts and pauses playback as expected.


## [2.6.6] - 2026-06-21

### Added
- **Guided setup wizard.** First launch now walks you through connecting to Anki, choosing a note type and field mapping, and fetching the recommended dictionary/frequency/pitch resources — replacing the old single-screen welcome dialog. Re-runnable anytime from Tools → Setup Wizard.
- **Japanese UI.** A complete Japanese (`ja`) translation, selectable in Settings → language picker (restart to apply). English stays the default; the translation catalog is drift-guarded in CI.
- **yt-dlp auto-update.** The YouTube downloader can self-update in the background so mining keeps working when YouTube changes, with a manual "Update yt-dlp" button and a configurable binary location for users who supply their own.
- **Card-styling "Off" preset.** A new Off option leaves your note template untouched. Styling now applies automatically on Save with an inline status line instead of a separate step.
- **Join Discord button** in the menu-bar corner for the community server.

### Changed
- **Media-agnostic wording.** "Anime" is renamed to "Video" across the UI, and `*arr`-style metadata tags are stripped from the card Source label — mining has always worked on any video, and the wording now reflects that.
- **Card styling defaults to Off.** Existing configs are migrated once; nothing is rewritten on your cards unless you pick a preset.

### Fixed
- **AV1 preview fallback.** When the preview pane can't decode an AV1 video, it now plays the audio and shows subtitles instead of a dead player. Mining is unaffected — screenshots still come from FFmpeg.
- **Nord theme input-field borders are visible again** (#85).
- **Custom AnkiConnect port honored in setup.** The setup wizard reads the AnkiConnect URL from config off the UI thread, so a non-default port connects correctly.


## [2.6.5] - 2026-06-19

### Added
- **Recommended-resources download on first run.** First launch offers to fetch the recommended starter resources (dictionary, frequency, and pitch-accent data) so a fresh install is ready to mine without hunting them down.
- **Restore dictionaries from disk.** Settings → Dictionaries detects indexed dictionaries present on disk but missing from your chain and offers a one-click Restore so they aren't silently dropped.
- **`ANKI_MINER_HOME` environment variable.** Point the app at a custom data directory instead of `~/.anki_miner/` — useful for portable setups or keeping data off the system drive.

### Changed
- **Window title shortened to "Anki Miner"** (was "Anki Miner - Japanese Vocabulary Mining Tool").
- **Settings save shows an inline status instead of a popup.** Saving updates a small inline "Saved" label rather than interrupting with a modal dialog.

### Fixed
- **AV1 video previews in-app when your GPU can decode it.** Machines with a hardware AV1 decoder (RTX-30+/Tiger-Lake+) now play AV1 in the preview pane. On a machine without one, the pane shows a short "this video uses AV1, which your system can't decode for in-app preview" notice instead of a blank player; if a frame does decode later, the preview recovers automatically. Mining is unaffected either way — screenshots come from FFmpeg, not the preview.
- **No more console windows flashing on Windows** (#79). yt-dlp, ffmpeg, ffprobe, and PowerShell subprocesses no longer pop up black console windows during mining.
- **Episode pairing no longer trips on codec/CRC tags in filenames** (#80). Bit-depth, codec, and CRC32 tags are stripped before the episode number is read, so files pair to the right episode.
- **Settings survive a corrupt config file.** On a damaged `gui_config.json`, the app recovers from a `.bak` (written before each overwrite) instead of resetting to defaults.
- **File dialogs open in a sensible folder.** Dictionary import and Browse dialogs open at the relevant directory (e.g. your dicts folder) instead of the filesystem root.
- **Unsaved settings edits are kept on refresh, and dictionary-chain reordering persists.**
- **Frequency and pitch data read correctly.** The frequency importer honors the source column order, and pitch-accent lookups use the lemma reading.
- **Cleaner definitions.** Duplicate kana glosses are removed and results are ranked by score.
- **Better subtitle parsing.** ASS/SSA comment lines are skipped and the katakana-noun filter is corrected, so junk lines don't become cards.
- **Cancel actually stops a run** even when triggered while the processor is still being built on the worker thread.
- **Undo reverts only the current session's cards**, not rows added by earlier sessions.
- **Partial downloads are rejected.** Downloaded files are checked against the server's Content-Length before use.
- **Frequency file picker again offers `.txt` files.**
- **Word-preview column sorting is disabled in grouped views** where it produced misleading order.
- **Anki warns when card creation returns fewer IDs than expected** instead of silently under-counting the vocab cache.
- **ffmpeg extraction failures and skipped duplicate cards are surfaced in the GUI** instead of failing silently.
- **Long mining and queue sessions are more stable** — several resource leaks and teardown races in the preview player, dictionary handles, and worker threads were fixed.
- **Imported-dictionary CSS is sandboxed** from loading external resources, so custom card styling can't pull in outside content.
- **Fewer Windows Defender false positives.** Release builds compile the PyInstaller bootloader from source and embed version metadata, avoiding AV-flagged prebuilt hashes.
- Minor visual polish: dark-theme table corner button and Reset-to-default button alignment.

### Removed
- **Windows portable `.zip` and generic Linux `.tar.gz` downloads.** Releases now ship one download per platform: Windows `Setup.exe`, Linux `.deb` (Debian/Ubuntu) or AppImage (other distros), macOS arm64 `.tar.gz`, plus PyPI and source. The Setup.exe installs per-user without admin, and the AppImage is the self-contained portable Linux option.


## [2.6.4] - 2026-06-14

### Fixed
- **Release builds no longer fail to fetch ffmpeg.** The Linux and Windows release jobs pinned a BtbN daily ffmpeg autobuild, which BtbN prunes after ~10 days, so the v2.6.3 build hit a 404 and produced no downloadable binaries. They now pin a BtbN month-end snapshot (kept long-term). No user-facing app changes; 2.6.4 ships the same application as 2.6.3 with working release binaries.


## [2.6.3] - 2026-06-14

### Added
- **Expression (word-level) audio on cards** (Issue #73). Opt-in native pronunciation audio for each card's Expression. Map an Anki field under Settings → Anki → Auxiliary Data Fields (auto-detects a note field named "expression_audio"), enable it in Settings → Audio, and every mined card gets a recorded reading of its word. The feature is triple-gated (an audio fetcher injected **and** expression audio enabled **and** the field mapped), so a default config writes byte-for-byte the same cards as before. Audio identity keys on the card's Expression form plus its reading, so homographs don't collide and words with no reading are skipped. Works across Episode, Batch, Deck Builder, YouTube, and Audiobook mining.
- **Configurable audio source chain** (Settings → Audio). Expression audio is fetched through an ordered chain of sources — imported local audio packs, then JapanesePod101, then Google Translate — each of which can be enabled, disabled, or reordered. The first source to return audio for a word wins. JapanesePod101 stays the zero-setup default.
  - **Local audio pack import.** Settings → Audio → Add Audio Pack imports a [local-audio-yomichan](https://github.com/themoeway/local-audio-yomichan)-compatible audio directory (ajt_japanese, nhk16, forvo_ja, jpod_legacy / jpod_alternate formats). Packs are indexed into `~/.anki_miner/audio_packs/` — the audio files themselves stay where they are. Multiple packs installed at once are queried in priority order (nhk16 > shinmeikai8 > forvo > jpod > jpod_alternate); when a parent folder holds several packs they are all imported at once. Packs can be reordered, disabled, or removed (index only — audio files are never deleted).
  - **Google Translate TTS source.** A free, no-key synthetic fallback for words no pack or JapanesePod101 covers. It is fed the kana reading (not the kanji), so pronunciation is correct and immune to homograph misreads. Disabled by default; shown in Settings → Audio as a built-in row that can be disabled but not removed.
- **Audiobook mining tab.** Queue local audiobook + subtitle pairs and mine them audio-only: no per-word screenshots, and the book's embedded cover art stands in as every card's Picture. Subtitles auto-fill from a same-stem file next to the audio. Stats and history record under a distinct Audiobook identity so audiobook runs don't mix with episode or YouTube history.

### Changed
- **Audio fetches reuse a single HTTP session.** Expression-audio lookups now share one `requests` session per run instead of opening a new connection per word, cutting handshake overhead on large mines.

### Fixed
- **JapanesePod101 audio downloads no longer 403 from the CDN.** Requests now send a browser User-Agent, so the audio CDN stops rejecting them.
- **Loanwords get the right JapanesePod101 audio.** The katakana reading is sent to the lookup, so words written in kana resolve correctly.
- **JapanesePod101 retries on a surface-form miss.** When the surface form returns nothing, the lookup retries with the UniDic lemma before giving up.
- **Audio sources are tried form-by-form, not source-by-source.** Each source is now tried against every candidate form of a word before moving on to the next source, so a higher-priority source isn't skipped just because the first form missed.
- **Fetched audio is validated before it is cached** (MP3 magic bytes, size cap, HTTPS-only redirects), so a non-audio response can't poison the cache.
- **Audio pack scan, fetch, and import edge cases hardened**, along with expression-audio cache writes and unique temp-file staging, so concurrent fetches and partial downloads don't clobber each other.
- **The i+1 filter checks all unknown lemmas, not just mineable ones** (#74), so a sentence with a second unknown word is correctly held back.
- **Card style presets are scoped to the miner's own glossary markup** so preset CSS no longer bleeds into unrelated content on the card.
- **Duplicate media filenames are no longer re-encoded** in batch stores, avoiding redundant ffmpeg work within a run.
- **A media-source file that vanished mid-run is counted as a store failure** instead of silently succeeding with no media.
- **A corrupt `anki_fields` value survives config load.** A non-dict value is replaced with defaults instead of crashing startup.
- **Negative delays are clamped and swallowed fetch errors are logged** instead of silently disappearing.

### Maintenance
- Internal refactors: an `ExpressionAudioFetcher` protocol with a chained fetcher implementation, and a shared queue mining-progress adapter extracted from the per-tab workers.
- Release CI-gate now waits for an in-flight CI run instead of failing the release outright.
- README title-icon polish; welcomed @Expri-commits to CONTRIBUTORS.md.

### Removed
- Obsolete `gifs/gif-tools/README.md`.


## [2.6.2] - 2026-06-12

### Added
- **Source field on mined cards** (#69). Opt-in Anki field recording where a mined word came from, as `<origin> @ HH:MM:SS` — for file mining the origin is `<folder> — <episode>`, for YouTube the video title. Mapped under Settings → Anki → Auxiliary Data Fields (auto-detects note fields named "source"/"origin"); nothing is written until a field is mapped.
- **Card styling via named presets.** The single default-stylesheet toggle is replaced by a preset picker — Default, Yomitan / Lapis Classic, Minimal / Clean, or None — backed by a `card_style_presets` registry. The old `use_default_card_stylesheet` boolean migrates to a preset id on load.
- **Review words before mining on YouTube** (#65). An opt-in checkbox brings the word-curation dialog to YouTube runs with full parity to batch mining: the embedded video player and multi-dictionary lookup are sourced from the video's fetched media.
- **YouTube playlist support** (#70). Pasting a playlist URL (`/playlist?list=…`) or a watch URL with a `list=` parameter expands into queue rows via yt-dlp `--flat-playlist`. A confirm dialog appears when the playlist exceeds the new `youtube_playlist_max` cap (default 100, Settings → YouTube). Mixed watch+list URLs (`/watch?v=…&list=…`) prompt whether to add just that video or the whole playlist. Videos already in the queue are skipped as duplicates. Mix/radio URLs (`list=RD…`) are treated as plain video links. Expansion and per-entry probing both run on background threads (`YouTubePlaylistResolveWorker`, `YouTubePlaylistProbeWorker`) so the GUI stays responsive. New helper `utils/youtube_url.py` classifies URLs without network access; new models `PlaylistInfo` and `PlaylistEntry` carry the flat-playlist metadata.

### Changed
- **Dependency floors raised to recent stable.** The `>=` minimums in `pyproject.toml` were bumped to recent battle-tested releases (pysubs2, requests, fugashi, PyQt6, yt-dlp, psutil, packaging, and the dev tools). Upward flexibility is preserved; yt-dlp keeps its `<2027.0.0` upper bound. The `black` and `lxml` floor bumps also clear CVE-2026-32274 and CVE-2026-41066 at the floor (per the 2026-06-10 supply-chain audit).
- **Minimum Python raised to 3.11** (3.10 dropped). Standalone bundles freeze their own interpreter, so source/PyPI installs now require Python 3.11+.
- **Cancelling now stops promptly across every mining mode.** Stop/Cancel propagates into the YouTube, single-episode, batch, and deck-builder pipelines, kills any in-flight ffmpeg child, and is re-checked during silent yt-dlp phases so a run no longer keeps working after you ask it to stop. Long-running subprocess calls (yt-dlp, ffmpeg, shortcut probes) are now bounded by timeouts.
- **Closing the window no longer abandons running work.** If a worker is still running at close, the window hides and the close is deferred until the thread exits, instead of tearing down a live thread mid-write.

### Fixed
- **Mining now validates the Anki note type and field mapping before processing starts** (#52). If the configured note type is missing or a mapped field is absent, the run fails immediately with a clear error instead of after full media extraction. The configured deck is also auto-created at this point.
- **Mining no longer crashes on non-ASCII (Japanese) filenames or stream titles.** Both ffprobe and ffmpeg output are decoded as UTF-8 with replacement, so cp932/cp1252 (Windows) no longer raises a decode error mid-extraction.
- **A failed pair in a batch no longer aborts the rest of the item or loses cards already made.** Each video/subtitle pair is guarded independently: a transient AnkiConnect failure on one episode is reported against that episode while the remaining episodes still process and earlier cards still count. Failed episodes are now surfaced in the batch and manual-pair completion summaries, and file selectors stay populated after a failed or preview-only run (#51).
- **Batch queue robustness.** Cancelling between episodes returns the item to *pending* (with its row redrawn) instead of marking it *completed*; queue rows are keyed by a stable id so duplicate series names update the right row; the worker owns item-status writes to close a re-pick race; and the Cancel button is shown during a Retry Failed run.
- **YouTube reliability.** A workspace-creation failure on one video no longer kills the whole queue; mid-run Clear/remove skips items still queued; a live-stream probe with no duration is handled instead of erroring; the fetch watchdog only kills its own process; `Stop` during a YouTube run shuts the pipeline down cleanly (and closing the app mid-curation releases the dialog instead of hanging the GUI); and a config change before the tab's first run no longer silently disables session stats.
- **Age-restricted YouTube videos are accepted when a cookies *file* is configured**, not only when cookies come from a browser. Duplicate playlist entries already queued (probe still in flight) are also skipped, preventing a double fetch of the same video.
- **Security: yt-dlp argument injection blocked.** Option-leading inputs (e.g. `--…`) are rejected before reaching yt-dlp, and Jisho API definitions are HTML-escaped before being interpolated into a card.
- **Card media no longer silently goes missing on large sessions.** Both note media and dictionary-supplied media upload through one byte-budgeted chunked path, and earlier card batches are persisted even when a later batch fails.
- **Dictionary / import resilience.** Read-only SQLite is opened via a percent-encoded URI so dictionary folders with spaces or special characters work; dictionary sqlite handles are released before a remove/re-import; pitch and frequency CSV imports are staged and only promoted once both succeed (leftover `.tmp` files are cleaned on failure); and an oversized dictionary `index.json` is capped while deriving the dictionary id.
- **Yomitan structured-content images sized correctly** (#68). Image dimensions are emitted as unit-carrying inline CSS honoring `sizeUnits`, so em-sized inline art (pitch-accent marks, ［派生語］ tags in 三省堂国語辞典 etc.) no longer renders huge, and fractional em sizes survive instead of truncating to zero. `verticalAlign`/`border` styles on images are no longer dropped.
- **Dictionary imports no longer abort on lone UTF-16 surrogates** (#67). Hand-converted term banks carrying unpaired surrogates used to kill the whole import with a sqlite UTF-8 encode error; they are now scrubbed to U+FFFD at the storage write seam.
- **Local databases survive being locked.** A run still succeeds (and stats/known-words still record) when `known_words.db`, `stats.db`, or another local DB is momentarily locked, and `gui_config.json` / `recent_files.json` are written atomically with `OSError` logged rather than swallowed. A word marked "known" now survives a known-words DB rebuild.
- **Settings reliability.** Saving a changed dictionary storage folder re-syncs the dictionary panel; removing a dictionary persists only the chain (not a full save); blacklist/whitelist selectors clear when no list is set; the Reimport-All worker joins its predecessor before restarting; and the previously dead Anki panel sync/test buttons now run validation. Repeated Test Connection / update checks no longer leak worker threads.
- **Config changes apply mid-session.** Editing settings rebuilds config-bound services and propagates the new config to non-Settings tabs, and an in-session subtitle offset is no longer reset by an unrelated config update.
- **Episode-number detection.** The fallback matcher takes the *last* bare number (so numeric titles like "86" or "Mob Psycho 100" don't steal the episode slot), tolerates `SxxEyy` separators, and now captures adjacent numbers (e.g. `Title 1 2`) that were previously skipped — while still ignoring resolution tokens like `720p`.
- **Word counting / furigana parity.** Compound words that can't be located in the sentence are dropped from lemma counts too (matching the card-build path), and noun furigana/readings are recomputed when an i+1 card swaps to the surface form.
- **Test isolation: probe-worker tests no longer poison the Qt app singleton.** Two YouTube probe-worker tests created a bare `QCoreApplication`, so a later widget test reused it and aborted with "Cannot create a QWidget without QApplication"; both now create a `QApplication` (still a `QCoreApplication`, so the thread/signal tests are unaffected).

### Maintenance
- **New app icon** — white 鉱 on black (Shippori Mincho B1), glyph embedded as outline paths so it no longer depends on the system's serif fallback font.
- **Supply-chain / CI hardening.** Workflow actions and pre-commit hooks pinned to commit SHAs; PyInstaller and appimagetool pinned (with checksum verification, plus nfpm checksum); tag-triggered workflows gated on green CI; per-OS release uploads fail on missing files; the bundled YouTube smoke test hard-fails instead of continue-on-error.
- **README**: demo GIFs rebuilt with audio-synced animation, recommended resources updated (Bee's Character Dictionary added), badge caching tuned.
- Welcomed @Geniusssmit and @cskings14 to CONTRIBUTORS.md.

### Removed
- **The `smoke-min-deps` CI job and floor-pin tooling** (`scripts/extract_floor_pins.py`). With dependency floors now at recent stable, floor-version smoke-testing is redundant with the latest-version `test` job.

## [v2.6.1] - 2026-06-07

### Added

### Changed

### Fixed
- **YouTube mining failing with "n challenge solving failed" / "Only images are available"** (#64). YouTube extraction needs a JavaScript runtime, but yt-dlp's `--js-runtimes` defaults to deno only. The fetcher now auto-detects an available runtime (node/bun/quickjs) on PATH and passes `--js-runtimes` to both the probe and download commands. Gated on the installed yt-dlp actually supporting the flag, so older yt-dlp installs are unaffected. No configuration required.
- **YouTube mining still failing after the runtime fix with "Remote component challenge solver script was skipped"** (#64, follow-up). A JS runtime alone is not enough: recent yt-dlp split YouTube challenge solving into the runtime plus the EJS solver script, which it no longer auto-downloads. The fetcher now also passes `--remote-components ejs:github` (gated on yt-dlp support) so the script is fetched and cached on first use. Added whenever supported — including deno-only setups, which need the script too. No configuration required.
- **Cards missing audio/picture on large YouTube batches** (connection reset). Large mining sessions overran AnkiConnect's request limit, resetting the connection so media silently failed to store. Media now uploads in chunks bounded by cumulative base64 size (~4MB) and action count, with failed chunks retried as single-file POSTs; any residual failures surface in a warning dialog instead of producing silently empty fields.
- **Long error text clipped in the YouTube queue rows** (#64). Queue rows now use an eliding label with a full-text tooltip, keeping row heights consistent instead of overflowing or clipping the message.

### Removed


## [2.6.0] - 2026-06-06

### Added
- **Bundled ffmpeg/ffprobe in standalone builds.** Windows installer/zip, macOS tarball, and Linux AppImage/tarball now ship ffmpeg + ffprobe — no separate ffmpeg install required. A new resolver (`ffmpeg_location` / `ffprobe_location` config fields) prefers explicit paths, then bundled binaries, then PATH. The `.deb` deliberately ships **without** bundled ffmpeg (GPL) and uses system ffmpeg. PyPI/`pipx` and source installs still need ffmpeg on PATH.
- **Text Size (UI font scale) control** (#63). Settings → Themes now has a Text Size dropdown that scales the whole UI — QSS font variables, curator row height, dialog fonts, and the subtitle overlay — via the new `ui_font_scale` config field (range 0.5–2.0).
- **Custom cookies for YouTube mining** (#62). Pull cookies from a browser profile or point at a cookies file to mine age-restricted / bot-gated videos.
- **Hiragana-only and katakana-only word exclusion filters** (#57). Optional filters under Settings → Filtering drop kana-only words (default off).
- **Word-curation popup in batch mining** (#60). The per-episode curation dialog (previously single-episode only) is now an opt-in step in batch runs, with per-item subtitle offset forwarded.
- **Bundled name wordsets from JMnedict** (#59). Proper-noun filtering ships with name wordsets generated from JMnedict; per-wordset checkboxes in the filtering panel control which are excluded (`excluded_wordsets`). Exclusion is matched on each word's mined surface form, so names are caught even when the dictionary lemma diverges from the surface.
- **Word Curator play/pause** (#55). The per-row embedded player gained a play/pause toggle.
- **Recent-file pairing remembers the subtitle offset** (#61). Re-selecting a recent video/subtitle pair restores its saved offset.
- **Wider subtitle offset range.** The offset control now spans ±300 seconds.
- **Bug/feature report moved to a top-bar button** for discoverability.

### Changed
- **Performance sweep.** Subtitle lines are tokenized once and threaded through the parser; MeCab and the dictionary chain pre-warm off the GUI thread after first paint; dictionary lookups batch via an IN-clause; `storeMediaFile`/media uploads batch through AnkiConnect multi; furigana/reading are memoized within a parse pass; Deck Builder caches per-file tokenization to avoid a double-parse; the Word Curator uses fixed-row height + debounced search.
- **Settings panels densified.** Tighter spacing and section headings across Anki/Dictionary/Filtering/Media/Queue/YouTube; duplicate tooltips removed.

### Fixed
- **AV1 in-app preview disabled** instead of spamming decode/CUDA errors; AV1 media still mines normally — only the in-app preview is skipped. Video preview now forces software decode.
- **Horizontal overflow of UI elements in the Episode Mining tab** (#56).
- **AppImage desktop shortcut broken on relaunch.**
- **"All Themes" button mapped to the wrong tab.**
- **Progress bar percentage glitch removed**; folder/file path bars equalized across tabs.
- **Max Sentence Duration tooltip corrected.**
- **"About Anki Miner" dialog improved.**
- **Word-curation cancel / empty-selection handling.** Cancelling a curation prompt no longer leaves a stray dialog to dismiss, and confirming with nothing selected is now recorded as a completed (zero-card) run rather than a cancellation — so batch status and stats stay accurate.
- GPL ffmpeg kept out of the `.deb`; resolver hardened (deterministic lookup order, bounded cache, prewarm join).

### Maintenance
- ffmpeg GPL license, written source offer, and About-dialog attribution added for the bundled binaries.
- CI fetches static ffmpeg per-OS and asserts bundled encoders (`libmp3lame`, `libopus`, `libsvtav1`, `libwebp`, `libwebp_anim`) in the release smoke test.
- Restored the dedicated CI lint job (ruff + black) so formatting is gated on PRs, not just the local pre-commit hook.
- pre-commit now runs black + `ruff --fix`.
- README showcase refreshed (retimed card GIFs, honest MP4 download links), recommended resources updated.
- Welcomed @sman68634 to CONTRIBUTORS.md.


## [2.5.0] - 2026-05-28

### Added
- **Deck Builder tab** — point at a folder of episode/subtitle pairs and mine a whole show into one named deck, frequency-ordered, deduped across episodes (inspired by jiten.moe / jpdb but free, using media you already have). Two-phase flow: Phase 1 aggregates lemma counts across every subtitle in the folder via `SubtitleParserService.count_lemmas` + `corpus_aggregator.aggregate`, ranks them, and offers three selection modes — **ALL**, **TOP_N**, or **COVERAGE_PCT** (cumulative % of in-corpus mineable tokens, not `frequency.csv`). A preview dialog shows what will land; Phase 2 mines each episode into the chosen deck via fresh `EpisodeProcessor` instances (with `anki_deck_name` and `include_known_words` swapped in via `dataclasses.replace`) and a curation closure that drops anything outside the selected set or already carded in a previous episode. New `AnkiService.ensure_deck` makes target-deck creation idempotent. New data models: `DeckSelectionMode`, `DeckBuildRequest`, `DeckBuildPreview`. New config flag: `include_known_words` (default off — known words are still filtered out unless `collection_filter=False`).
- **Word Curator: embedded video player + multi-dictionary lookup** (#41, #43). Each row in the curation dialog now exposes a `SubtitlePlayerWidget` that plays the source line in-place so you can hear the sentence before deciding. The lookup panel shows every enabled offline dictionary's hit side-by-side via `DefinitionService.lookup_all_offline`, not just the first-hit-wins result that lands on the card. Jisho online is excluded from this multi-lookup to keep curation interactive.
- **Custom Anki card styling** (#44). Generated cards now ship with built-in styling for the standard fields. The template renders cleanly out of the box without manual Anki note-type customization.
- **Local user-curated known-words list** (#42). The Word Curator now has an "Add to Known Words" action; words added this way are stored in `known_words.db` with `source='user'` and applied on every mining run regardless of `config.use_known_words_db`. Survives "Rebuild Known Words DB" (which now calls `clear(preserve_user=True)`). Reset via **Settings → Filtering → Manage Known Words → Reset User List**. Stored as `mined_form` (POS-aware: verbs/adjectives = lemma, nouns = surface) so the list rolls up against the in-process filter set cleanly.
- **Yomitan pitch-accent zip importer**. Settings → Dictionary → Pitch Accent File now accepts a Yomitan-format pitch-accent zip in addition to raw CSV/TSV. Imports run on a background `PitchImportWorker` (`CancellableWorker` subclass) driven through a modal `QProgressDialog` so the GUI stays responsive and the import is cancellable. Result is written to `~/.anki_miner/pitch_accent.csv`; existing format paths are unchanged.
- **Configurable dictionary store location** (#45). The folder that holds your installed dictionaries (`dicts_root`, defaulting to `~/.anki_miner/dicts/`) is now an explicit setting. Useful if you want to keep multi-gigabyte dictionary indexes on a different drive than the rest of `~/.anki_miner/`.
- **Dracula and Alucard themes**, plus every previously-added theme exposed in the Themes panel (#48). Built-in theme count is now 29 across 10 families.
- **Exclude specific Anki decks from known-word lookup** (#38). New filter under Settings → Filtering pulls excluded deck names through `AnkiService._build_vocab_query`, which negates each name in the underlying `findNotes` query. Anki's `deck:` matches subdecks, so a parent exclusion covers nested decks without an explicit `::*` clause. Notes that live in both an included and an excluded deck are excluded entirely (Anki's `-deck:` is note-level). Works for deck names containing `_` and `*` (escaping fixed).

### Changed
- **README** now documents the recommended **pitch accent** sources (Kanjium accent list as raw TSV; アクセント辞典v2 as a Yomitan zip) and **frequency list** sources (JPDB v2.2 Frequency Kana; BCCWJ SUW LUW Combined) with install paths into `~/.anki_miner/`.

### Fixed
- **Batch mining pairing error** (#39). The episode-number pairing path in `FilePairMatcher.find_pairs_by_episode_number` and `EpisodeMatcher.match_by_episode_number` now consumes each subtitle at most once (`used_subtitles` set), so a same-episode collision can't collapse every video onto the first subtitle.
- **`addNotes` duplicate error aborting a batch**. AnkiConnect's batch endpoint surfaced a `duplicate` error when a candidate word was already in the deck and re-encountered later in the same session, killing the rest of the batch. Filter + dedup now share the cached existing-vocabulary set and drop the candidate client-side before AnkiConnect ever sees it.
- **Deck-exclusion query broke on deck names with `_` or `*`**. The query builder now escapes Anki's wildcard characters before injecting them into `-deck:"…"` clauses.
- **`DeckBuilderWorker` lifecycle on app close**. Closing the main window mid-build no longer leaks the worker; the worker is torn down on app close and `build_finished` is suppressed on cancel so the GUI doesn't try to render results from a half-built run. Coverage tooltip wording clarified.
- **Deck Builder built far fewer cards than the preview promised**. The per-episode pipeline still applied its optional filters (frequency rank, word lists, sentence dedup, cross-episode counts, i+1, sentence length) during Phase 2, so a preview promising 2,401 cards would land closer to 51 once i+1 cut everything. Two new config flags fix this: `bypass_optional_filters` (skips those filters when the worker requests "complete deck" mode) and `allow_duplicate_cards` (posts notes with `options={"allowDuplicate": True, "duplicateScope": "deck"}` so words already carded elsewhere in the collection re-card into the new deck). Known-words subtraction is unaffected and still respects `include_known_words`. Defaults are off; standard mining behaviour is unchanged.
- **Deck Builder tab stuck on "Cancelling…"**. The button state did not advance back to "Build" if the worker terminated through a non-cancel exit path (clean finish, exception). The state machine now resets on every worker `finished` signal.
- **Progress bar stranded or jumped backwards across all mining tabs**. Stage progress was reported per-stage from `EpisodeProcessor` and re-normalised in each tab, so percentages jumped on stage transitions and tabs disagreed on what 50% meant. Replaced with a shared `orchestration/stage_weighted_progress.py` helper that maps per-stage progress to a single monotonic 0–100 across the run; both Single Episode and Deck Builder tabs now drive a uniform progress bar through `_mining_tab_base`.

### Maintenance
- Welcomed @BDubbB, @Joywinbarboza, and @chicorykvass to CONTRIBUTORS.md.
- Star-on-GitHub button copy tightened for clarity.
- DeckBuilderTab unified `worker_thread` attribute and added an integration test covering the second-preview-cancels-lingering-worker case.

## [2.4.7] - 2026-05-26

### Added
- **"Star on GitHub" button pinned to the menu bar corner**: a `⭐ Star on GitHub` `QToolButton` sits in the top-right corner of the main menu bar and opens the project repo in the default browser. Drive-by: Help → "Report an Issue" renamed to "Report a Bug / Suggest a Feature" so the feature-request flow is discoverable from the same menu.

### Changed
- **Jisho online fallback now disabled by default for new installs**: the default `dictionary_chain` ships with the JMdict offline entry enabled and the Jisho fallback **disabled**. The dictionary settings panel surfaces the trade-off in-line ("⚠ rate-limited, slower" badge; "Offline dictionaries are recommended; they're faster than Jisho.") to steer new users toward an offline-first setup. **No effect on existing users** — `gui_config.json` persists the per-user chain, so anyone who already had Jisho enabled keeps it. Re-enable from Settings → Dictionaries.
- **Settings panel copy polished across every tab** (Anki, Dictionary, Filtering, Media, Queue, YouTube): redundant tooltips that duplicated the visible helper text were removed and helper text shortened to one declarative sentence per field. No functional changes.

### Fixed
- **Batch mining skipped words on files with resolution tokens in the filename** (Issue #36): `EpisodeNumberExtractor.extract_from_filename` ran its season/episode regexes against the raw stem, so tokens like `1280x720` parsed as `season=1280, episode=720` and `720p` parsed as episode `720`. The bogus episode number then collided with per-episode dedup state and silently dropped words from the batch when the Sentence Length Filter was active. Resolution patterns (`\d{3,4}[xX]\d{3,4}`, `\b\d{3,4}[pi]\b`) are now stripped from the stem before the episode-number patterns run.
- **Batch progress bars stranded after cancel or success**: cancel and success paths in `BatchProcessingTab` left both `current_progress_widget` and `overall_progress_widget` displaying the final percentage instead of resetting to the Ready state. Both terminal paths now call `reset()` on both widgets before the summary dialog fires.
- **Single Episode tab retained the previous run's file paths after Process**: this made it easy to accidentally re-mine the same files on the next click. `SingleEpisodeTab` now clears both `video_selector` and `subtitle_selector` after a successful run, so the next session starts from an empty form.
- **`ProgressWidget.setMaximum(N)` propagated `N` to the underlying `QProgressBar`** even though the bar's format is `"%p%"` (percent), producing visually-broken progress on any non-100 maximum. The bar now stays on a 0–100 scale regardless of caller-supplied count; the total is still tracked internally on `_total_items` for ETA math.
- **Section header right-edge clipping**: `SectionHeader` had a `0` right margin, so its trailing widget sat flush against the section frame. Bumped to `SPACING.xs` to match the left padding.

### Maintenance
- **GitHub Actions bumps** (Dependabot, gh-actions group): `actions/checkout` v4 → v6 and `actions/setup-node` v4 → v6 in `.github/workflows/contributors.yml`. No behavior change.

## [2.4.6] - 2026-05-25

### Added
- **Manual audio track override** (Issue #35): the Single Episode tab now exposes a "Tracks" button next to "Test Timing". Clicking opens a modal listing probed audio streams (codec, language, channel layout) with Auto-detect as the default. The chosen track persists per-tab in memory, resets when the video file changes or after Process completes, and flows through both the mini-player preview (`SubtitleViewer`) and the extraction worker (`MediaExtractorService.process_episode`) so preview and final cards use the same audio source. Override targets are validated by `audio_index` against a cached `list_audio_streams()` probe; a missing-stream override falls back to Japanese auto-detect with a warning rather than aborting. `SubtitleViewer` additionally falls back to `QMediaMetaData.Key.Language` when both override and ffprobe yield no Japanese track — handy for containers whose ffprobe-visible language tags are stripped but whose Qt demuxer can still identify the track.

### Changed
- **Performance — UI rendering** (`analytics_tab`, dialogs, panels): table population loops now wrap `setUpdatesEnabled(False)` + `setSortingEnabled(False)` with a `try/finally` restore, the Word Preview search box debounces filter+repopulate by 150ms, Analytics caches the last refresh for 5s to skip redundant DB reads on rapid tab clicks, and Themes panel star toggles do surgical button + cell updates instead of rebuilding the whole tree. New test file `tests/unit/test_ui_perf_regressions.py` locks in the contracts.
- **Performance — startup**: `stats_service.load()` now runs via `QTimer.singleShot(0, ...)` after the window paints, the YouTube tab builds its `EpisodeProcessor` lazily on first Mine click (not at construction), and `DictionaryRegistry.load()` reads each `index.sqlite`'s meta table via a `meta.json` sidecar cache (skipping SQLite opens when nothing changed). First-paint time on cold start reduced accordingly.
- **Performance — per-episode mining**: `known_word_db.sync_with_anki()` accepts a pre-fetched `existing` set to skip the redundant internal `get_known_words()` scan; the orchestrator merges new Anki vocabulary in memory rather than re-reading the DB. `SubtitleParserService` hoists `generate_furigana(text)` and `generate_reading(text)` outside the per-word loop (one MeCab pass per line, not N). AnkiConnect note-creation batch size bumped from 50 to 100 (roughly halves HTTP round-trips). Two new SQLite indexes on `mining_history` speed up history queries.

### Fixed
- **Frequency filter now excludes unindexed words** (Issue #34): when `Max Frequency Rank` is set, words absent from the frequency list are filtered out instead of bypassing the cutoff. Previous "benefit of the doubt" behavior contradicted the GUI tooltip ("Only mine words within the top N most frequent") and produced uncommon words in mined decks even with strict caps. Set Max Frequency Rank back to 0 to restore the prior pass-everything behavior.
- **Audio track override could be ignored after the source file was replaced**: `MediaExtractorService._audio_stream_list_cache` retained probed streams for the service lifetime, so swapping the file on disk between selecting a track and running Process left the resolver matching against stale ffprobe output (silently falling back to JP auto-detect). The cache is now invalidated at the start of each `process_episode` run via the new `invalidate_audio_stream_cache()` method, preserving the within-run double-probe fix from 2e0cc13.

## [2.4.5] - 2026-05-24

### Added
- **Sentence length filter** (Issue #33): new Settings → Word Filtering → "Sentence Length" section lets you drop cards whose example sentence exceeds an audio-duration cap (seconds), a character cap, or both — to reduce deck size and keep reviews snappy. Master toggle plus two independent caps; either cap set to `0` means "no limit" (the spinboxes render `0` as "No limit"). Runs **after** the i+1 sentence filter so the enforced cap applies to the FINAL chosen sentence (i+1 swaps each word's sentence/duration to its chosen line, so an earlier check would be silently bypassed by the swap). New config fields: `use_sentence_length_filter`, `max_sentence_duration_seconds`, `max_sentence_chars` (default off; no behavior change for existing users).
- **Theme variants grouped under family nodes**: Settings → Themes now renders a `QTreeWidget` where themes sharing a `family` field (e.g. light/dark variants of the same palette) nest under an expandable parent row. The active theme's family auto-expands on open. The family row exposes a **tri-state star** — empty / half / full reflects how many of its variants are favorited; clicking toggles the whole family in a single batch (one `favorites_changed` emission, not one per variant). Family star uses a `QGraphicsOpacityEffect` dim pass so the icon stays readable on dark themes. New optional theme-schema fields: `family`, `variant`. Existing single-file themes without these fields continue to render as flat top-level rows.
- **Yomitan-format frequency dictionaries**: Settings → Word Filtering → Frequency Source now accepts a Yomitan frequency zip in addition to the existing `frequency.csv`. The importer parses `term_meta_bank_*.json`, normalizes the three Yomitan rank shapes (integer, `{value: N}`, `{frequency: {value: N}}`), and writes the result to `~/.anki_miner/frequency.csv`. Rejects archives with `format != 3` (v1/v2 use a different `term_meta_bank` schema and would parse silently into garbage) and missing-`format` headers with a typed `SetupError`. Ranks `<= 0` are treated as display-only rather than producing nonsense "0th most common word" entries. Zip extraction reuses the same path-traversal / size-cap hardening as the dictionary importer.
- **Background-threaded frequency import**: the Yomitan freq import now runs on a new `FrequencyImportWorker` (`CancellableWorker`) driven from `SettingsTab._resolve_frequency_path` via a modal `QProgressDialog` + nested `QEventLoop` — the caller stays blocking while the GUI thread stays responsive and the user can cancel. Overwrite confirmation prompt fires if `frequency.csv` already exists. Save flow reordered so the disk-mutating import runs last, after the candidate config is fully built, so a future validation failure can abort before touching `~/.anki_miner/frequency.csv`.

### Fixed
- **Theme colours not applied to the Analytics tab**: the Analytics page's tables, headers, and chart container inherited the host `QWidget` palette instead of the active theme's surface/text variables, so dark themes left Analytics rendering with light backgrounds. Added scoped Analytics selectors to `common.qss` that resolve against the theme variable set, so every theme now drives Analytics consistently.

## [2.4.4] - 2026-05-23

### Fixed
- **Dictionary "Remove" failed on Windows after mining** (Issue #30 follow-up to the 2.4.3 menu wiring): `DefinitionService` providers cache a read-only SQLite connection on `index.sqlite`, and Windows holds the file lock for the lifetime of that handle. Settings → Remove therefore worked on a freshly-launched app and failed with `[WinError 32]` once the user had run a single mine. `DefinitionService.close()` now walks the provider chain calling each provider's `close()` (probed via `getattr`, so Jisho is silently skipped) and resets `_loaded` so the next mine re-opens the chain. `DictionarySettingsPanel.set_release_callback` accepts a pre-remove hook injected by `app.py`; `MainWindow.release_dictionary_resources` fans the hook out to every mining tab. Tabs return `False` while a worker is in flight, surfacing a "Stop the mining run first" dialog instead of corrupting the in-flight processor.
- **Dictionary "Re-import" failed on Windows after mining** (Issue #32): same root cause as #30 — `yomitan_importer.import_yomitan_zip`'s atomic directory rename fails with `Access denied` while any provider still holds the old `index.sqlite` open. All three re-import entry points (per-row Re-import, legacy "Reimport JMdict", and "Reimport All") now call `DictionarySettingsPanel.request_resource_release()` before dispatching the worker; refusal surfaces the same "Stop the mining run first" dialog.
- **`release_dictionary_resources` was a no-op on `SingleEpisodeTab` and `BatchProcessingTab`**: the #30/#32 release hook only closed the live YouTube tab processor. The other tabs returned `True` without touching their cached worker, so a user who mined a file or a batch (without YouTube) and then tried to Remove or Re-import still hit the original lock. Both tabs now grab the finished worker's processor (`worker_thread.processor` for single, `episode_processor`/`_current_processor` for batch) and call `definition_service.close()`. In-flight workers still short-circuit to `False` — closing providers under a live processor would crash the run.
- **CI smoke-min-deps job pin drift**: the smoke job hardcoded `yt-dlp==2026.3.3 psutil==5.9.0`, which silently diverges from the `>=` floors declared in `pyproject.toml`. New `scripts/extract_floor_pins.py` reads `project.dependencies`, extracts the `>=` lower bound for each name in `FRAGILE`, and prints `name==X.Y.Z` specs the workflow `pip install`s directly. The smoke test glob was also broadened from two named files to `tests/unit/test_youtube_*.py` so new YouTube unit tests are covered automatically.

## [2.4.3] - 2026-05-22

### Added
- **Multi-URL YouTube queue**: paste several URLs into the YouTube tab, watch each one probe its title and resolved subtitle source on Add, then mine the whole list sequentially in one click. Replaces the single-URL `YouTubeWorkerThread` path. Per-item retry-once absorbs transient yt-dlp fetch errors before the row is marked ERROR; cancellation halts the in-flight item via `psutil` process-tree kill (covers yt-dlp's ffmpeg child) before tearing down the per-attempt workspace under `media_temp_folder/youtube/run-<uuid>/`. Each retry attempt allocates its own workspace and cleans up in its own `finally`, so a failed first attempt cannot leak into the second. `YouTubeQueueItem` uses `eq=False` so identity-based `list.remove()` survives status mutations.
- **Theme settings panel**: new Settings → Themes lists every shipped theme plus any user JSON dropped under `~/.anki_miner/themes/`. Click a row to live-preview across the whole app; the star toggle adds the theme to `theme_favorites`, which populate the header `QComboBox` and the `Ctrl+T` cycle order. A Revert button snaps back to whatever theme was active when the panel opened. Persisted as `theme` and `theme_favorites` in `gui_config.json`; `AnkiMinerConfig.__post_init__` coerces the favorites JSON list back to a tuple for frozen-dataclass safety.

### Fixed
- **No discoverable per-row re-import for dictionaries** (Issue #29): the per-row "Re-import" button added in 2.4.2 only rendered on stale-schema rows, leaving users who imported their dictionaries before 2.4.2 with no way to seed `source.zip` — "Reimport All" skipped them and pointed at a stale-row button they did not have. Dictionary rows now expose a right-click context menu with **Re-import…** and **Remove** for indexed entries (Yomitan and JMdict). The "Reimport All" skip dialog now points users at the new menu instead of the stale-row button.
- **"Remove" did nothing on dictionary rows** (Issue #30): the Dictionary Settings panel only exposed delete via a button bound to the selection model, so users who interacted with rows outside the main selection path had no way to remove a dictionary. Indexed rows (Yomitan, JMdict) now expose the right-click context menu introduced for #29; Jisho rows have no menu since the online fallback cannot be reimported. Remove calls a Windows-aware `_robust_rmtree` that clears `S_IWRITE` on read-only files and retries through transient `[WinError 32]` from lingering SQLite handles, then invalidates the registry cache and emits `dictionary_removed` (distinct from `chain_changed`) so the settings tab persists the trimmed chain immediately.
- **Bold target word landed on the wrong characters in `SentenceFurigana` when a bracketed reading sat adjacent to the target** (Issue #31): the 2.4.1 bold-target feature (#20) reused subtitle-side cursor arithmetic that did not account for `[reading]` markup inserted by the furigana pass, so `SentenceFurigana` wrapped `<b>` around the kanji of the adjacent token whenever the target was preceded by a bracketed reading. `wrap_target_furigana` in `utils/text_utils.py` now resolves each token's span against the bracket-expanded string via `str.find` from a running cursor, then assigns tokens to pre/body/post buffers by containment within the resolved `[start:end)` window.
- **Star toggle in the Themes panel was visually clipped**: the favorites star in Settings → Themes inherited the global `QTableWidget::item { padding: 8px; }` rule, which cropped the top of the glyph and made the star look broken. Added a scoped `QTableWidget#themesPanelTable::item { padding: 0; }` override plus a new `QToolButton#starToggle` rule set in `common.qss` (transparent chrome, muted text color, warning color on `:checked`) so the star sizes to the row height cleanly. Drive-by perf fix: `Theme.get_stylesheet()` now caches the raw `common.qss` bytes once and memoizes the substituted output per mode, so previewing a theme by clicking a row no longer re-reads the 1100-line QSS file and re-runs the variable-substitution regex on every click. `Theme.set_user_dir` invalidates the per-mode cache on user-dir swap.
- **YouTube progress bar stuck after Clear / cancel / unhandled worker exception**: queue-end cleanup (worker handle release, button-text reset, progress widget reset) was wired only to `YouTubeQueueWorker.queue_finished`, which is emitted from inside `run()` on the success path. Mid-fetch cancel and unhandled worker exceptions exited via `QThread.finished` without ever clearing the progress widget, so the tab stranded on `"Merging…"` / a stale percentage with a leaked worker handle. Cleanup now lives in a dedicated `_on_worker_finished` slot wired to `QThread.finished` so it fires on every exit path; `queue_finished` keeps the success-path summary log. The Clear Completed action additionally resets the progress widget when the queue is idle, but intentionally leaves the live `Mining N of M…` display alone if a mid-run clear happens while an item is still processing.

### Removed
- Single-URL `YouTubeWorkerThread` (replaced by `YouTubeQueueWorker`).

## [2.4.2] - 2026-05-21

### Added
- **Saved-source dictionary archives and "Reimport All"**: `yomitan_importer.import_yomitan_zip` now persists the source zip alongside `index.sqlite` at `~/.anki_miner/dicts/<dict_id>/source.zip` — staged through the same atomic rename as the index, so a crash mid-import never leaves a mismatched pair. The Dictionary Settings panel's "Reimport JMdict" button becomes "Reimport All": walks the chain, dispatches one chained import per indexed entry, and surfaces three result buckets (reimported / skipped-no-source / failed) in a single summary dialog when the batch completes. Dictionaries imported before this release have no saved source — use the per-row stale-reimport button once to re-pick the zip; the second import seeds the cached source so subsequent "Reimport All" sweeps pick the dictionary up automatically.

### Fixed
- **Bold target word wrapped the wrong characters on subtitle lines with internal spaces** (Issue #20): MeCab strips whitespace from its token stream, so `SubtitleParserService`'s cursor walked left of every internal space and bolded the wrong substring — `素直` rendered as ` 素`, `通す` as ` 通`, `真っ赤` as `な 顔`. Both `parse_subtitle_file` and `parse_subtitle_file_with_index` now locate each token's span via `str.find(surface, cursor)` from a running cursor, so spans land on the actual morpheme regardless of source spacing.
- **Bold-target helper text and tooltip rendered literal `<b>` markup as HTML**: `FormPanel` helper labels defaulted to `QLabel` AutoText, which silently interpreted the example `<b>...</b>` as bold rich text instead of showing the literal markup users need to read. Helper labels are now forced to `Qt.TextFormat.PlainText`; the checkbox tooltip escapes its angle brackets. Copy was also tightened to "Wraps mined word in `<b>...</b>` in Sentence and SentenceFurigana fields."
- **Silent no-op when re-mining an already-known episode with bold-target enabled**: enabling the toggle and re-mining an episode whose words were all already in the collection produced zero new cards (correct) with no visible explanation, so users mistook the silent inaction for "the bold setting is broken." `EpisodeProcessor` now warns when every parsed word is already in Anki, noting that card-format options only apply to newly-mined cards. `AnkiService` additionally logs a per-batch counter (`bold_target_in_sentence=on: precomputed bold used on X/Y cards`) so a diagnostic log reveals whether the parse populated the bolded fields.
- **Monolingual dictionary glosses missing line breaks** (Issue #28): plain-text Yomitan monolingual dictionaries (e.g. `旺文社国語辞典`) store each entry as a single string with `\n` separators between numbered sub-senses. The renderer HTML-escaped the gloss but left the newlines literal, which Anki's WebView collapses to a single space — multi-sense entries rendered as a visually-dense block. Newlines are now converted to `<br>`, matching Yomitan's own card output. `\r\n` and bare `\r` are normalized to `\n` first so Windows-authored dictionaries render identically. Structured-content dictionaries are unaffected (they use Yomitan's native `br` node). **Re-import affected monolingual dictionaries** after upgrading to refresh the cached HTML; existing cards in your Anki collection are not rewritten retroactively.
- **`yt-dlp` floor version**: bumped from `2026.3.0` to `2026.3.3` in `pyproject.toml` (and the CI floor-version smoke job) to skip an upstream regression in the 2026.3.0–3.2 line.

## [2.4.1] - 2026-05-19

### Added
- **Contributor onramp polish**: added `SECURITY.md` documenting the GitHub private security advisories flow, `.github/pull_request_template.md` with a maintainer-aligned checklist, `.github/ISSUE_TEMPLATE/config.yml` (blank issues disabled; Discussions and security-advisory contact links surfaced), `.github/CODEOWNERS` for auto-assignment, `.github/dependabot.yml` for weekly pip and GitHub Actions updates, `docs/TESTING.md` extracting the full test strategy from scattered notes, and `docs/RELEASING.md` documenting the maintainer release SOP including PyPI trusted-publishing and bundled installer artifacts. Issue templates converted from markdown to YAML forms so structured fields render natively in the GitHub issue UI.
- **Audio quality + format control** (Issue #18): new Settings → Media controls for audio bitrate (32-320 kbps) and codec (`mp3` | `opus`). Opus produces substantially smaller files at equivalent perceived quality — 64 kbps Opus is roughly comparable to 128 kbps MP3 for speech, useful for bulk-mined collections approaching the AnkiWeb media quota. Defaults to MP3 @ 192 kbps to preserve existing behavior; opt in to Opus via Settings. The extractor probes ffmpeg for `libopus` once per session and hard-fails audio extraction with a clear error if the encoder is missing. Opus output is downmixed to stereo (`-ac 2`) so 5.1-surround source tracks common in anime BD/WEB-DL releases encode correctly — libopus rejects multi-channel input without explicit channel mapping. MP3 path preserves source channel layout. New config fields: `audio_format`, `audio_bitrate`. Existing card collections are untouched; only newly-mined cards use the configured format/bitrate.
- **Multi-dictionary export** (Issue #17): new optional `Glossary` Anki field is populated with every enabled offline dictionary's hit concatenated as Yomitan-format HTML (`<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="…">…</li></ol></div>` per dict), making the result compatible with the Senren dictionary-toggle UI. Jisho online lookup acts as a fallback only when no offline dictionary returned a hit. Existing `MainDefinition` field keeps first-hit-wins behavior — opt in by entering a field name in Settings → Anki Settings → Glossary Field. Empty by default (no behavior change for existing users).
- **Bold target word in subtitle sentences** (Issue #20): the mined word is now wrapped in `<b>…</b>` inside the `Sentence` and `SentenceFurigana` Anki fields so it stands out on the card front. The bolded range is the exact MeCab span of the mined morpheme (post compound-merge), not a string search — so when the same surface appears twice in one sentence, only the token that was actually mined is bolded. Opt in via Settings → Word Filtering → "Bold target word in sentence". New config field: `bold_target_in_sentence` (default off; no behavior change for existing users).
- **Match animated screenshot duration to audio**: new Media Settings toggle clips the animated screenshot to span the full audio range (subtitle window plus `audio_padding` on both sides) instead of the fixed `screenshot_animated_clip_duration`. Useful when subtitle lines are short and the static cap leaves the visual under-running the audio. New config field: `screenshot_animated_match_audio` (default off). Only takes effect when `screenshot_animated` is enabled.

### Changed
- **Verbs and adjectives now mine as their dictionary form (lemma)** (Issue #19). A subtitle line like `胸のとこ破れそう` previously created an Anki card with Expression `破れ`; it now produces `破れる` so the learner studies the headword that recognizes every conjugation. Nouns continue to mine as the surface form to preserve the Issue #5 fix (unidic-lite occasionally maps homographs like `豪腕` to a different lemma `剛腕`). Selection lives in `TokenizedWord.mined_form`. Side effects of the cleanup pass: lemma-only dedup inside the parser (surface no longer blocks a different lemma); `WordFilterService.filter_unknown` checks lemma only against the existing Anki vocabulary; `WordFilterService.deduplicate_by_sentence` keys on the NFKC-normalized, whitespace-collapsed sentence so punctuation/spacing variants no longer slip through; cards with empty-string definitions are now skipped (previously only `None` was filtered); compound-merge synthetics (`_merge_noun_suffixes`, `_merge_prefix_compounds`) reconstruct their lemma from each component's `feature.lemma` instead of overwriting it with the merged surface — the verb-nominalizer merge (`方/手/様`) keeps its surface-as-lemma behavior because the merged form (`言い方`) is itself the dictionary headword; `_extract_lemma`'s hyphen-strip now only fires when the tail after the hyphen is ASCII, so Japanese names like `メル-ビル` are no longer truncated. Word Curator column labels updated to `Word (mined)` and `Form in subtitle` to surface which field becomes the Expression. Legacy surface-form cards in user collections are not migrated; re-mining a previously-surface-mined verb will create a second card with the lemma until the user reconciles.
- **`CONTRIBUTING.md` rewritten** to cover the actual workflow (branches, conventional commits as a preference, changelog contribution, pre-commit), with cross-links to the new testing/security/code-of-conduct docs. README's contributing section was uplifted to surface architecture, testing, code of conduct, and security documents.
- **Black and ruff `line-length` raised from 100 to 120 in `pyproject.toml`** and the codebase reformatted accordingly. No behavior changes.
- **AnkiConnect existing-vocabulary lookup is now cached per `AnkiService` instance**. The unknown-word filter step previously issued one `findNotes` + `notesInfo` round-trip per candidate word — up to ~50 round-trips per session against AnkiConnect on a typical episode. The vocabulary set is now fetched once and reused; `create_cards_batch` and `delete_notes` both invalidate the cache so undo and re-mine still see fresh state. User-visible effect: noticeably faster mining on episodes with many candidate words.

### Fixed
- **AnkiConnect "duplicate" error aborting batch card creation**: when a candidate word was already present in the deck and re-encountered later in the same session (or re-introduced after partial dedup), AnkiConnect's batch endpoint could surface a `duplicate` error that aborted the entire batch and left the session in an inconsistent state. The filter and de-dup passes now share the cached existing-vocabulary set, so duplicates are dropped client-side before AnkiConnect ever sees them.
- **Glossary field GUI label**: corrected a stray helper-text typo on the Glossary field input in Anki Settings.

### Removed
- Dead-code cleanup: removed `FolderProcessor` and its same-folder pairing path, unused `HistoryService` query methods, `StatsService.get_series_stats`/`get_series_progress`, the orphan `queue_changed` signal, ~20 unused widget setters/properties, single-card `AnkiService.create_card` (batch path is canonical), service `lookup_batch` test-only methods, `DictionaryRegistry.list_dicts`, `WordListService.get_blacklist`/`get_whitelist`, `WordFilterService.filter_by_length`, orphan `config_exists`/`delete_config`/`reset_cancellation` methods, the never-raised `NoJapaneseSubsError`, and the obsolete `use_offline_dict` config field (legacy values are silently stripped on load).

## [2.4.0] - 2026-05-16

### Added
- **Multi-dictionary support**: load Yomitan-format dictionaries via Settings → Add Dictionary…. Installed dictionaries live under `~/.anki_miner/dicts/<dict_id>/index.sqlite` and are discovered on startup by `DictionaryRegistry`.
- **Reorderable provider chain** (first-hit-wins) replacing the fixed JMdict→Jisho fallback. The chain is persisted as `dictionary_chain` in `gui_config.json` and can mix any number of indexed dictionaries with the Jisho online fallback in any order.
- **Structured-content HTML rendering**: Yomitan structured-content entries are rendered to HTML on import so card definitions preserve Yomitan's formatting (definition lists, examples, tags) instead of falling back to plain text.
- **Custom Anki tags** (Issue #14): a new "Custom Tags" field in Anki Settings applies whitespace-separated tags to every mined card. Defaults to `auto-mined`; empty string disables tagging. Persists as `anki_tags` in `gui_config.json`.
- **Animated screenshots** (Issue #13): opt-in AVIF or WebP animated clips replace the static JPEG screenshot when enabled in Media Settings. Configurable clip duration, fps, height, and quality; capped by the subtitle line's duration. Requires ffmpeg built with `libsvtav1` (AVIF) or `libwebp_anim` (WebP) — missing encoder logs a clear error rather than producing broken files. New config fields: `screenshot_animated`, `screenshot_animated_format`, `screenshot_animated_clip_duration`, `screenshot_animated_fps`, `screenshot_animated_height`, `screenshot_animated_quality`.
- **Pitch category romaji toggle**: a new Anki Settings toggle switches the `pitch_category` field between Japanese labels (平板/頭高/中高/尾高/起伏, default) and Yomitan/Lapis-compatible romaji (heiban/atamadaka/nakadaka/odaka/kifuku). Persists as `pitch_category_format` in `gui_config.json`.
- **Multi-select in Word Curator** (Issue #12): the Word Curation dialog now supports `Ctrl+Click`, `Shift+Click`, and `Ctrl+A` for selecting multiple rows, plus bulk Accept/Reject/Blacklist shortcuts on the selection.
- **Full-text tooltips in Analytics page** (Issue #11): hovering a truncated cell in the Analytics tables now reveals the full value via tooltip.

### Changed
- **JMdict storage**: now uses a SQLite index at `~/.anki_miner/dicts/jmdict-english/index.sqlite` instead of parsing the XML on every startup. Legacy `~/.anki_miner/JMdict_e` is auto-migrated on first launch.
- **`DefinitionService` API**: now requires an explicit provider list at construction. `DictionaryRegistry.build_provider_chain(config)` is the canonical builder.

### Removed
- **In-memory `JMdictProvider`**: replaced by the SQLite-backed `IndexedDictProvider`. Existing users are auto-migrated on first launch; the legacy XML can be deleted after migration.

### Fixed
- **Yomitan dictionary card formatting now matches Yomitan/Lapis structure.** Each definition field is wrapped as `<div class="yomitan-glossary"><ol><li data-dictionary>…</li></ol></div>` with `gloss-sc-*` classes on every structured-content element and the Yomitan `<a class="gloss-image-link">` envelope around images. Re-import all existing dictionaries (Yomitan and JMdict) to pick up the new formatting — the chain UI flags any dictionary that needs it.
- **Yomitan structured-content rendering preserves more of the source formatting**. Cards generated from Yomitan dictionaries previously lost most of the original visual structure. Four bugs in `yomitan_renderer.py`:
  - **Allowed-tag set was too narrow**: `ruby`/`rt`/`rp`/`rb`, `dl`/`dt`/`dd`, `thead`/`tbody`/`tfoot`, `details`/`summary`, and `h1`–`h6`/`p` collapsed to `<span>`, so furigana base+reading concatenated inline (`子こ供ども達たち`) and definition-list "forms" ran together.
  - **Inline `style` was dropped entirely**, which gutted monolingual-JP dicts that depend on per-span font-size, color, vertical-align, and list-style-type for sense markers and headword styling. Now passed through a CSS-property whitelist with `url()`/`expression()`/`javascript:`/`vbscript:`/`data:`/quote/brace scrubbing and a 256-char value cap.
  - **Yomitan's `data: {k: v}` was misrendered as concatenated CSS class fragments** (`class="data-content-definition"`) instead of HTML `data-*` attributes, so dictionary-supplied CSS hooks did nothing. Now emits proper `data-foo="bar"`.
  - **Tag badges had no separation**, so a row of tags rendered as `nouncolloquialpoliteabbr.Kansai`. Tags are now surfaced by the provider as an italic `<i>(tag1, tag2, dictName)</i>` line above the gloss list, matching Yomitan's default Anki handlebars partial.
  - Also added: `lang` attribute, `colspan`/`rowspan` on `td`/`th`, `open` on `details`, `alt`/`width`/`height`/`title` on `img`, `title` on common containers.

  **Existing Yomitan dictionaries must be re-imported** to pick up the new HTML — content is rendered at import time and lives in the dictionary's `index.sqlite`.
- **About dialog and update banner showed stale version after upgrade on Windows** (Issue #10): frozen `__version__` resolved through `importlib.metadata.version("anki-miner")`, which reads `*.dist-info/METADATA` off disk. Inno Setup overlay installs left `anki_miner-OLD.dist-info` next to the new one and filesystem enumeration picked the older entry, so 2.3.4 reported itself as 2.3.3 and re-offered its own update. `__version__` is now a hardcoded literal in `anki_miner/__init__.py` (pyproject reads it via `[tool.setuptools.dynamic]`); the Windows installer now wipes every `*.dist-info` dir before copying new files, so the same trap cannot reappear for any dependency. AppImage, `.deb`, and pip installs were never affected.
- **`ModuleNotFoundError: No module named 'packaging'` on pipx install** (Issue #15): `packaging` was previously assumed available via setuptools, but pipx-isolated venvs strip setuptools after install, so `update_checker`'s version comparison crashed on first launch. `packaging>=21.0` is now declared as an explicit runtime dependency in `pyproject.toml`.
- **AppImage subprocesses failed with `OpenSSL_3.3.0 not found`** (Issue #16): PyInstaller's bootloader prepended its bundled `_internal/` directory to `LD_LIBRARY_PATH`, and that value leaked into every spawned subprocess (yt-dlp, ffmpeg) where it shadowed the host's newer OpenSSL with our older bundled `libcrypto`, breaking system binaries linked against OpenSSL ≥ 3.1. `gui/app.py` now restores `LD_LIBRARY_PATH`/`DYLD_LIBRARY_PATH` from their `*_ORIG` snapshots before any subprocess can spawn.

## [2.3.4] - 2026-05-15

> **Note:** Windows builds of 2.3.4 reported their version as 2.3.3 in the About dialog and re-offered themselves as an update. Packaging bug, not a feature regression. Fixed in the next release (#10).

### Added
- **Subtitle regex filter** (Issue #8): a new "Subtitle Text Filtering" section in Word Filtering settings strips noise from subtitle lines before they reach the tokenizer. Use it to drop speaker tags like `(Tanaka)`, sound descriptions like `[door slams]`, music markers `♪♬`, or `Speaker:` prefixes. Four one-click presets seed common patterns; click multiple to stack them with `|`. Pattern, replacement string, and an enable toggle persist in `gui_config.json` (`subtitle_regex_filter`, `subtitle_regex_replacement`, `use_subtitle_regex_filter`). Replacement uses Python backreferences (`\1 \2`), not asbplayer's `$1 $2` — translate when copying patterns. Both the mining path and the subtitle viewer honor the filter.
- **`ExpressionReading` and `SentenceReading` Anki fields** (Issue #7): plain hiragana readings of the surface form and the full sentence, generated alongside the existing furigana fields. Matches Yomitan's `{reading}` style for users whose card template wants kana without the bracketed kanji[reading] markup. Two new Anki field mappings are exposed in Anki Settings.

### Fixed
- **Frequency column sorts numerically in Word Curator** (Issue #6): the Frequency Rank column now sorts as numbers instead of lexicographic text, so 100 no longer comes before 20. Unranked rows (`-`) cluster at the bottom regardless of sort direction. Implemented via a `QTableWidgetItem` subclass that stores the rank in a sort role and overrides `__lt__`.

### Changed
- **Card field mapping helper text**: the "Optional Card Fields" section in Anki Settings was renamed to "Auxiliary Data Fields" and the helper text now says exactly which files (`pitch_accent.csv`, `frequency.csv`) need to live in `~/.anki_miner/` for these fields to populate. Per-field placeholders dropped the redundant "(optional)" suffix.

## [2.3.3] - 2026-04-25

### Added
- **Smart Download button**: the in-app update banner now deep-links to the asset matching your install method. The button reads "Download .deb", "Download AppImage", "Download installer", or "Download archive" instead of opening the GitHub releases page; pip and source installs still see "View release". Detection is inline: AppImage env var, then `sys.frozen` per platform, then pip.
- **Skip this version**: a new banner button suppresses the prompt for that release. Re-enabling "Check for updates on startup" in Settings clears the skip so the next release prompts again.
- **Post-update confirmation**: on first launch after an upgrade, a one-shot dialog confirms the new version and links to the release notes. The new version is persisted before the dialog opens, so a crash mid-dialog does not re-fire the message. First launch ever (no previous version on disk) writes silently.
- **Settings checkbox**: "Check for updates on startup" is now user-reachable in the Settings tab and bound to the existing `check_for_updates` config field.

### Changed
- **Version comparison**: switched from naive integer-tuple parsing to `packaging.version.Version`, so prerelease (`2.4.0-rc1`) and post-release (`2.3.5.post1`) tags compare correctly.
- **GitHub API request**: now sends a `User-Agent: anki-miner/<version>` header alongside the existing accept header.

### Fixed
- **Pronouns mined**: 代名詞 like 彼, 誰, 何, 我々, 貴様 now appear in vocabulary output. Hiragana-only pronouns (これ/それ/ここ/...) remain filtered as noise via the existing kanji-required floor.
- **Compound nouns reassembled**: 刑務所, 爆発的, 芸術的, 死傷者, 入院中, and similar 名詞+接尾辞 forms now mine as a single word. Previously fugashi split them into base noun + suffix and the suffix was filtered out, leaving meaningless bases (e.g. 刑務 alone).
- **Prefix compounds mined**: 不可能, 無関心, 非常識, 反社会, 超能力, etc. now mine as one word. Whitelisted prefix surfaces (`無 不 非 反 超 未 新 旧 全 半 副 元 再 最`) merge with following 名詞/形状詞.
- **Verb-stem nominalizations mined**: 言い方, 読み方, 生き方, やり方 etc. now mine as one word. 動詞 + 接尾辞(名詞的) where suffix surface is `方`, `手`, or `様` is merged into a single 名詞.
- **Anki "Expression" field uses the subtitle's surface form**: when the dictionary lemma differs from what appeared on screen (e.g. 豪腕 vs lemma 剛腕), cards now show the variant the user actually saw. Edge case: orthographic variants encountered in different episodes may produce duplicate cards; uncommon enough to accept.
- **Expression furigana now matches the surface form**: previously `ExpressionFurigana` was generated from the lemma, so for words like 豪腕/剛腕 the card front would render the lemma reading even though the Expression text was the surface. Now both fields agree.
- **Existing-user migration for `allowed_pos`**: users upgrading from 2.3.2 with the legacy default `allowed_pos` saved in `gui_config.json` will be auto-migrated to the new default that includes 代名詞, so the pronoun fix actually reaches them. Custom user-edited POS lists are preserved untouched.

### Internal
- Fixed an incorrect test fixture in `test_includes_pronouns_by_default` that mocked the wrong unidic POS layout (real unidic-lite emits `pos1=代名詞, pos2=*`, not `pos1=名詞, pos2=代名詞`). The test now reflects production tokenizer behavior.
- Test count: 935 → 994 (+59 new tests covering compound merging, prefix merging, verb nominalization, real-fugashi integration, config migration, Expression-furigana surface usage, install-target detection, asset matching, banner singleton reuse, and Skip-this-version persistence).

### Known limitations
- Cards mined before this release with the wrong-kanji Expression (e.g. 剛腕 instead of 豪腕) are not auto-repaired. Re-running mining on the same content will skip them as "already known" via the lemma match. Manual deletion + re-mine is required to refresh those cards.

## [2.3.2] - 2026-04-24

### Breaking
- Removed the `min_word_length` config field and the "Minimum Word Length" spinbox in Word Filtering settings. Real vocab is mined regardless of length; single-kanji content words are always admitted, while single-character kana and katakana onomatopoeia remain filtered as noise.

### Fixed
- `GUIConfigManager.load_config` now silently drops config keys unknown to the current `AnkiMinerConfig` dataclass instead of raising `TypeError` and falling back to full defaults. Previously any removed field would silently reset the user's entire configuration (deck name, paths, Anki Connect URL) to defaults.
- **Long file paths in `FileSelector`**: QLineEdit now displays the start of long paths instead of scrolling to the tail, and the basename label elides with middle ellipsis while exposing the full name via tooltip.
- **Word Preview row numbers**: vertical header now sizes large enough for the bold digit font, fixing top/bottom clipping of numbers in the Discovered Words table.
- **Export dialog**: path input displays the start of long export paths and sets a tooltip with the full path.
- **QSS**: added a `QHeaderView::section:vertical` override so vertical headers no longer inherit horizontal-header padding, and symmetric padding on `QLineEdit` family inputs.

## [2.3.1] - 2026-04-23

### Fixed
- **Single-character kanji mining**: setting `min_word_length=1` now admits single kanji (e.g. 皿) as intended. A hardcoded filter previously rejected single kanji regardless of the configured floor. Single-character kana (hiragana, katakana) remain filtered as noise.

## [2.3.0] - 2026-04-22

### Changed
- **DefinitionService**: now self-initializing via `ensure_loaded`; callers no longer need to invoke setup explicitly.
- **Folder pairing**: consolidated into `FilePairMatcher` for consistent logic across batch entry points.

### Removed
- **BREAKING — CLI removed**: `mine` and `mine-folder` subcommands deleted. `anki_miner_gui` is now the sole entrypoint.

### Fixed
- **Temp media isolation**: each run gets its own temp directory so cross-run cleanup can't delete in-flight files.
- **Optional resource files**: warn on missing frequency/pitch accent files instead of failing silently.
- **Wheel packaging**: all GUI resources now bundled via `MANIFEST.in`.

## [2.2.0] - 2026-04-16

### Added
- **JSON-based theme system**: themes defined as JSON files with dynamic discovery, validation, and color variable extraction. Replaces per-theme QSS files with a single `common.qss` using `${color-*}` substitution.
- **Tokyo Night theme**: fourth built-in theme based on the VS Code Tokyo Night color scheme.
- **Theme loader tests**: unit tests for theme validation, discovery, and color extraction.
- **App showcase GIFs**: restored to README.

### Changed
- **Pitch accent fields**: updated to match Lapis card type field names.
- **Frequency/pitch accent errors**: clear error messages instead of silent failures.
- **UI element consistency**: improved visual consistency across all themes.
- **Theme backgrounds**: fixed all themes incorrectly showing a dark background.
- **Known-word filtering**: fixed known words not being filtered due to differing card field names.

### Removed
- **Per-theme QSS files**: `light_theme.qss`, `dark_theme.qss`, `sakura_theme.qss` replaced by the JSON theme system.
- **IconProvider dead code**: unused icon provider module and references removed.

### Fixed
- **CI workflow**: PyQt6 compatibility in GitHub Actions.
- **mypy**: resolved type error in `Theme.get_colors` return type; removed a stale `type: ignore`.

## [2.1.0] - 2026-04-01

### Added
- **Analytics dashboard**: series difficulty ratings and progress tracking.
- **Export system**: deck export plus enhanced CSV/TSV and vocab list export.
- **Known-word database**: SQLite-backed known-word cache with blacklist/whitelist and sentence deduplication.
- **History and undo**: mining history with undo support.
- **Subtitle viewer** with configurable card fields.
- **Auto-update** check and in-app issue reporting.
- **Comprehension %** and cross-episode frequency metrics.
- **Workflow improvements**: retry, drag-and-drop, recent files list.
- **Cancel button**, word curation dialog, and keyboard shortcuts.
- **Pitch accent and frequency** card fields with support for additional dictionaries.
- **Download methods**: improved install/download flow.
- **ARCHITECTURE.md**: design and architecture documentation.

### Changed
- **Text backgrounds**: redesigned for readability.

### Fixed
- Twelve post-merge test failures (mock passthrough and parameter rename).
- CI workflow adjustments for the executables build.

## [2.0.4] - 2026-02-13

### Changed
- Version bump to ship the icon files added in 2.0.3.

## [2.0.3] - 2026-02-13

### Added
- Windows (`.ico`) and macOS (`.icns`) icon files for PyInstaller builds.

## [2.0.2] - 2026-02-13

### Fixed
- Windows PyInstaller build no longer fails when no `.ico` file is available — falls back gracefully.

## [2.0.1] - 2026-02-13

### Added
- **PyPI publishing** workflow and **PyInstaller builds** for one-click installation.

### Changed
- Improved GIF descriptions in `README.md`.

## [2.0.0] - 2026-02-12

Initial public release.

### Added
- **CLI interface** with `mine` and `mine-folder` subcommands.
- **PyQt6 GUI** with single episode, batch processing, and settings tabs.
- **Morphological analysis** using Fugashi (MeCab) for Japanese word tokenization.
- **Smart word filtering**: filters by part of speech, excludes pronouns/particles/onomatopoeia, skips words already in Anki.
- **Parallel media extraction**: concurrent ffmpeg screenshot and audio capture with configurable worker count.
- **Offline dictionary**: JMdict XML support with Jisho API fallback.
- **Batch processing**: automatic video/subtitle file pairing with queue-based multi-series support.
- **Preview mode**: inspect discovered words before creating cards.
- **Subtitle offset**: per-episode timing adjustment for out-of-sync subtitles.
- **Three GUI themes**: Light, Dark, and Sakura.
- **AnkiConnect integration**: batch card creation with media embedding.
- **Lapis note type support**: default field mapping for the Lapis open-source note type.
