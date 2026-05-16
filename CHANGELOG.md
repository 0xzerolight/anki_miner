# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Multi-dictionary support**: load Yomitan-format dictionaries via Settings → Add Dictionary…. Installed dictionaries live under `~/.anki_miner/dicts/<dict_id>/index.sqlite` and are discovered on startup by `DictionaryRegistry`.
- **Reorderable provider chain** (first-hit-wins) replacing the fixed JMdict→Jisho fallback. The chain is persisted as `dictionary_chain` in `gui_config.json` and can mix any number of indexed dictionaries with the Jisho online fallback in any order.
- **Structured-content HTML rendering**: Yomitan structured-content entries are rendered to HTML on import so card definitions preserve Yomitan's formatting (definition lists, examples, tags) instead of falling back to plain text.

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
