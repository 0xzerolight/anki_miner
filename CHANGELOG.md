# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [2.0.0] - 2026-02-06

Initial public release.

### Added
- **CLI interface** with `mine` and `mine-folder` subcommands
- **PyQt6 GUI** with single episode, batch processing, and settings tabs
- **Morphological analysis** using Fugashi (MeCab) for Japanese word tokenization
- **Smart word filtering** — filters by part of speech, excludes pronouns/particles/onomatopoeia, skips words already in Anki
- **Parallel media extraction** — concurrent ffmpeg screenshot and audio capture with configurable worker count
- **Offline dictionary** — JMdict XML support with Jisho API fallback
- **Batch processing** — automatic video/subtitle file pairing with queue-based multi-series support
- **Preview mode** — inspect discovered words before creating cards
- **Subtitle offset** — per-episode timing adjustment for out-of-sync subtitles
- **Three GUI themes** — Light, Dark, and Japanese-inspired
- **AnkiConnect integration** — batch card creation with media embedding
- **Lapis note type support** — default field mapping for the Lapis open-source note type
