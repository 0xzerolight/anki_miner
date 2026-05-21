# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

### Changed

### Fixed

### Removed

## [2.4.3] - 2026-05-21

### Fixed
- **No discoverable per-row re-import for dictionaries** (Issue #29): the per-row "Re-import" button added in 2.4.2 only rendered on stale-schema rows, leaving users who imported their dictionaries before 2.4.2 with no way to seed `source.zip` — "Reimport All" skipped them and pointed at a stale-row button they did not have. Dictionary rows now expose a right-click context menu with **Re-import…** and **Remove** for indexed entries (Yomitan and JMdict). The "Reimport All" skip dialog now points users at the new menu instead of the stale-row button.

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
