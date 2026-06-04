# <p align="center">Anki Miner</p>

[![CI](https://github.com/0xzerolight/anki_miner/actions/workflows/ci.yml/badge.svg)](https://github.com/0xzerolight/anki_miner/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/anki-miner.svg)](https://pypi.org/project/anki-miner/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Code of Conduct](https://img.shields.io/badge/code%20of%20conduct-Contributor%20Covenant%203.0-blueviolet.svg)](CODE_OF_CONDUCT.md)
[![GitHub stars](https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social)](https://github.com/0xzerolight/anki_miner/stargazers)

Turn native Japanese content into Anki vocabulary cards - with screenshots, sentence audio, furigana, pitch accent, and frequency data.


Please leave a ⭐ star if Anki Miner helped you - it helps others find it.


## Showcase

![Anki Miner Showcase](gifs/anki_miner_showcase.png)

### Example cards

| | | |
|---|---|---|
| ![Cowboy Bebop](gifs/cowboy_bebop.gif) | ![Frieren](gifs/frieren.gif) | ![Steins;Gate](gifs/steins;gate.gif) |

## Installation

### Requirements

- **ffmpeg** on PATH.
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: [download from ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH.
- **Anki** with the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on. In Anki: **Tools → Add-ons → Get Add-ons**, paste code `2055492159`, restart.

### Download

Grab the installer for your platform from the [latest release](https://github.com/0xzerolight/anki_miner/releases/latest):

| Platform | Installer | Portable |
|----------|-----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` | `AnkiMiner-Windows-x86_64.zip` |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` | `AnkiMiner-*-Linux-x86_64.AppImage` |
| Linux (other) | — | `AnkiMiner-Linux-x86_64.tar.gz` |
| macOS (Apple Silicon) | — | `AnkiMiner-macOS-arm64.tar.gz` |

No Python required. Installers and portable archives bundle all dependencies.

<details>
<summary><strong>Install from PyPI (Python 3.10+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
```

</details>

<details>
<summary><strong>Install from source</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install .
```

</details>

## Quick Start

After installing, launch **Anki Miner** from your Start Menu, Applications folder, or app menu. If you installed from PyPI or source, run `anki_miner_gui` from a terminal. A desktop shortcut is created on first launch; re-run it from **Tools → Create Desktop Shortcut…** inside the app.

Anki must be running with AnkiConnect installed before mining starts.

Tabs:

- **Episode Mining**: mine one video/subtitle pair with file selectors and progress tracking.
- **Batch Mining**: queue a folder of episode/subtitle pairs for sequential processing. Files are paired by episode number, so each folder / queue item should hold a single show (use Multi-Anime Queue for mining multiple series at a time).
- **Deck Builder**: point at a folder of episode/subtitle pairs and mine the full series into one named deck. Lemmas are ranked by in-series frequency; pick how many to include (all, top N, or a coverage target) and preview before cards are created.
- **YouTube**: paste one or more URLs, then mine the queue.
- **Analytics**: history, series difficulty rankings, milestones, undo.
- **Settings**: Anki, Media, Dictionary, Filtering, YouTube, Themes. Saved to `~/.anki_miner/gui_config.json`.

## Features

- **Deck Builder**: point it at a folder with all episodes of a show and it mines the whole series into a single named deck, frequency-ordered, deduped across episodes. Inspired by the approach of jiten.moe / jpdb — free, using media you already have.
- Anki cards with furigana, pitch accent, and word frequency.
- **Bold the target word** in the sentence so it stands out on the card front.
- **Glossary field** that combines every enabled dictionary into one card field, compatible with the Senren dictionary-toggle template.
- **Word Curator with embedded video player** — hear each sentence before you decide, and see every enabled offline dictionary's hit side-by-side.
- **Custom card styling** — generated cards render cleanly out of the box; no manual Anki note-type styling required.
- **User-curated known-words list** — add words straight from the curator; they're skipped on every future run.
- Load any Yomitan dictionaries you like, reorder them, and optionally enable Jisho as a slower, rate-limited online fallback (offline dictionaries are recommended for speed).
- YouTube queue: paste a list of URLs, mine the whole list in one click.
- Batch a folder of episode/subtitle pairs for unattended processing.
- Review and edit the word list before any cards are created.
- Audio in MP3 or Opus, at the bitrate you choose — Opus produces much smaller files for the same listening quality.
- Animated screenshots in AVIF or WebP for cards that show a moment of motion instead of a still frame.
- Analytics dashboard with history, undo, milestones, and series difficulty rankings.
- 29 built-in themes (Catppuccin, Gruvbox, Nord, Tokyo Night, Rosé Pine, Dracula, and more) with a favorites list, `Ctrl+T` to cycle, and custom themes from a JSON file. See the full list below.

<details>
<summary><strong>Built-in themes (29)</strong></summary>

- **Ayu** — Light, Mirage, Dark
- **Catppuccin** — Latte (light); Frappé, Macchiato, Mocha (dark)
- **Dracula** — Dracula, Alucard
- **Everforest** — Light, Dark
- **GitHub** — Light; Dark, Dark Dimmed
- **Gruvbox** — Light Medium, Dark Medium
- **Kanagawa** — Lotus (light), Wave (dark)
- **Rosé Pine** — Dawn (light); Main, Moon (dark)
- **Solarized** — Light, Dark
- **Standalone** — Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Upstream attribution: [LICENSE-THEMES.md](LICENSE-THEMES.md). 
Custom themes: drop a JSON file matching the schema in `anki_miner/gui/resources/styles/themes/` into your config dir. Or suggest in an Issue and it will be added for all users.

</details>

<details>
<summary><strong>How It Works</strong></summary>

1. **Read the subtitles** and split Japanese into individual words.
2. **Filter** to content words you don't already know.
3. **Grab a screenshot and audio clip** from the video for each line.
4. **Look up definitions** in your configured offline dictionaries, optionally falling back to Jisho online if enabled (slower, rate-limited).
5. **Send the finished cards to Anki.**

</details>

## Dictionaries

Anki Miner looks up definitions through a **provider chain** you configure. Each lookup tries the providers in order; the first hit wins. Load any number of offline Yomitan-format dictionaries — these are recommended for speed. Jisho is available as an online fallback but is disabled by default because every lookup waits ~0.5s on the API and slows mining substantially.

Add a dictionary in **Settings → Add Dictionary…** by pointing at a Yomitan `.zip` archive. Drag entries to reorder the chain. Installed dictionaries are indexed once into `~/.anki_miner/dicts/<dict_id>/index.sqlite` and loaded on startup. Structured-content entries are rendered to HTML on import, so card definitions preserve the source dictionary's formatting (definition lists, examples, tags). The dictionary folder location is configurable via **Settings → Dictionary → Dictionary Folder** — useful for keeping multi-GB Yomitan archives on a separate drive.

**Recommended Japanese → English dictionaries** — both are JMdict-derived; pick whichever fits your cards, or load both and order them as you like:

- **[Jitendex](https://github.com/Jitendex/Jitendex)** — modern JMdict successor with structured-content formatting, example sentences, and richer tags. Best for visually rich cards. Grab the Yomitan archive from the [Jitendex releases page](https://github.com/Jitendex/Jitendex/releases).
- **[JMdict](https://www.edrdg.org/jmdict/edict.html)** — the original community JMdict project. Plain-text glosses, smaller index, faster to add. Yomitan builds are available from the [Yomitan dictionary list](https://learnjapanese.moe/yomichan/#dictionaries) or you can rebuild from the EDRDG source.

Install via **Settings → Add Dictionary…** in either case.

The bundled name wordsets used for proper-noun filtering (given names, surnames, place names, org/product names) are derived from [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html), part of the JMdict/EDICT project (EDRDG), CC BY-SA 4.0.

## Pitch accent & frequency data

Anki Miner can attach pitch-accent patterns and frequency ranks to each card. Both are loaded from user-supplied files that live at `~/.anki_miner/pitch_accent.csv` and `~/.anki_miner/frequency.csv`. The Settings pickers accept either a raw CSV/TSV in the expected column shape or a Yomitan-format zip; Yomitan zips are converted to CSV automatically on Save.

### Pitch accent

Pick a source and drop it into **Settings → Dictionary → Pitch Accent File**:

- **[Kanjium accent list](https://github.com/mifunetoshiro/kanjium)** - the community pitch dump as a raw TSV at `data/source_files/raw/accents.txt` (~124k entries, `kanji<TAB>reading<TAB>pattern`). Smallest install: rename to `pitch_accent.csv` and drop straight into `~/.anki_miner/`, no Yomitan import step needed.
- **[アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries)** - TheMoeWay's "Recommended" pitch dictionary as a Yomitan zip; richer notation than Kanjium. Point the picker at the `.zip`.

Install via **Settings → Dictionary → Pitch Accent File** - the picker accepts raw CSV/TSV or a Yomitan zip (auto-imported to `~/.anki_miner/pitch_accent.csv` on Save).

### Frequency lists

Pick a source and drop it into **Settings → Filtering → Frequency List File**:

- **[JPDB v2.2 Frequency Kana](https://github.com/Kuuuube/yomitan-dictionaries)** - Kuuuube's "Recommended" frequency list and the real successor to JPDB v2. Good default for mixed media mining.
- **[BCCWJ SUW LUW Combined](https://github.com/Kuuuube/yomitan-dictionaries)** - large balanced-corpus rank list from the Balanced Corpus of Contemporary Written Japanese. Complement for users also reading news or novels.

Install via **Settings → Filtering → Frequency List File** - the picker accepts raw CSV/TSV or a Yomitan zip (auto-imported to `~/.anki_miner/frequency.csv` on Save).

## YouTube Mining

Paste one or more URLs into the YouTube tab. Each row shows its title, length, and subtitle source as you add it; click **Mine** to process the whole list. Transient download errors are retried once before a row is marked failed. Cancel is safe at any point.

Manual Japanese subtitles are used when available. Auto-captions are accepted only when YouTube generated them natively from Japanese audio — captions that YouTube produced by machine-translating from another language are skipped, because they don't make usable cards. Even native auto-captions are rougher than manual subtitles, since they lack sentence boundaries.

Gotchas:

- **Bot-detection prompts**: if YouTube asks "Sign in to confirm you're not a bot", open **Settings → Cookies → Browser** and pick Firefox or Chrome. Anki Miner pulls cookies from that browser's profile on every fetch.
- **Age-restricted videos**: same fix.
- **Max duration**: defaults to 120 minutes. The probe aborts before downloading if the video is longer. Adjust in Settings.

## Updates

Anki Miner checks GitHub for new releases on startup (toggle in Settings). When an update is available, a banner offers a one-click download of the asset that matches your install: `.deb` for Debian/Ubuntu, `.AppImage` for AppImage, the Inno installer on Windows, the macOS arm64 archive, or the release page for pip/source installs. "Skip this version" suppresses the prompt for that release; the next release prompts again.

## Troubleshooting

| Issue                    | Solution                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| "Cannot connect to Anki" | Start Anki and ensure AnkiConnect is installed.                                  |
| "Deck not found"         | Create the deck in Anki or update the deck name in Settings.                     |
| "Note type not found"    | Configure your note type's field names in Settings → Anki.                       |
| "ffmpeg not found"       | Install ffmpeg and add it to PATH.                                               |
| No definitions found     | Add a Yomitan dictionary in Settings → Add Dictionary… (recommended), or enable the Jisho fallback (slower, rate-limited). |
| Audio is wrong language  | The tool tries Japanese audio tracks first, then falls back to the default.      |
| Subtitles out of sync    | Use the subtitle offset control in the GUI.                                      |

## Contributing

Contributions are welcome — bug fixes, dictionary integrations, GUI polish, doc improvements, all sizes.

- New here? Start with [CONTRIBUTING.md](CONTRIBUTING.md).
- Architecture overview: [ARCHITECTURE.md](ARCHITECTURE.md).
- Testing strategy: [TESTING.md](TESTING.md).
- Code of Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- Security: [SECURITY.md](SECURITY.md).

Bug reports and feature requests → [Issues](https://github.com/0xzerolight/anki_miner/issues).
General questions and discussion → [Discussions](https://github.com/0xzerolight/anki_miner/discussions).

## Special Thanks

Sincere thanks to people who made exceptional contributions to the project:

★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Brilliant feature suggestions, new release testing, community building

See [CONTRIBUTORS.md](CONTRIBUTORS.md) for everyone who has made any kind of contribution to the project.


## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
