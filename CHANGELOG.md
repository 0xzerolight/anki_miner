# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [2.1.0] - 2026-04-16

### Added
- **JSON-based theme system**: themes defined as JSON files with dynamic discovery, validation, and color variable extraction. Replaces per-theme QSS files with a single `common.qss` using `${color-*}` substitution.
- **Tokyo Night theme**: fourth built-in theme based on the VS Code Tokyo Night color scheme.
- **Theme loader tests**: unit tests for theme validation, discovery, and color extraction.
- **App showcase GIFs**: restored to README.

### Changed
- **Pitch accent fields**: updated to match Lapis card type field names.
- **Frequency/pitch accent errors**: clear error messages instead of silent failures.
- **UI element consistency**: improved visual consistency across all themes.
- **Theme backgrounds**: fixed all themes incorrectly showing dark background.

### Removed
- **Per-theme QSS files**: `light_theme.qss`, `dark_theme.qss`, `sakura_theme.qss` replaced by JSON theme system.
- **IconProvider dead code**: unused icon provider module and references removed.

### Fixed
- **CI workflow**: fixed PyQt6 compatibility in GitHub Actions.

## [2.0.0] - 2026-02-06

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
