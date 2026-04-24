# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

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
