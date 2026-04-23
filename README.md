# Anki Miner

[![PyPI version](https://img.shields.io/pypi/v/anki-miner.svg)](https://pypi.org/project/anki-miner/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

Batch-mines Japanese vocabulary from anime and YouTube into Anki cards. Given a season folder or a YouTube URL, it produces cards containing screenshots, sentence audio, furigana, pitch accent, and frequency data.

Suited to batch processing after viewing, rather than real-time lookup during playback (the asbplayer and Yomitan workflow).

## Showcase

![Anki Miner Showcase](gifs/anki_miner_showcase.png)

### Example cards

| | | |
|---|---|---|
| ![Cowboy Bebop](gifs/cowboy_bebop.gif) | ![Frieren](gifs/frieren.gif) | ![Steins;Gate](gifs/steins;gate.gif) |

*Generated from video and subtitle files. Each card contains a screenshot, sentence audio, furigana, and definition.*

## How It Works

1. **Parse subtitles**: tokenize Japanese text with MeCab morphological analysis.
2. **Filter words**: keep content words (nouns, verbs, adjectives, adverbs); drop words already in your Anki collection or on your blacklist.
3. **Extract media**: capture screenshots and audio clips from the video at each subtitle's timestamp via ffmpeg.
4. **Fetch definitions**: look up English definitions from JMdict (offline) or the Jisho API.
5. **Create cards**: batch upload to Anki via AnkiConnect.

## Features

- Lapis-compatible cards with furigana, pitch accent, and word frequency fields.
- YouTube support: paste a URL, mine the video.
- Queue a folder of episode/subtitle pairs for sequential processing.
- Offline JMdict dictionary with Jisho API fallback.
- Preview and curate the word list before any cards are created.
- Parallel ffmpeg extraction for screenshots and sentence audio.
- Analytics dashboard with history, undo, and series difficulty rankings.
- Four themes (Light, Dark, Sakura, Tokyo Night) plus custom JSON themes.

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

After installing, launch **Anki Miner** from your Start Menu, Applications folder, or app menu. If you installed from PyPI or source, run `anki_miner_gui` from a terminal. A desktop shortcut is created on first launch; re-run it from **Tools → Create Desktop Shortcut...** inside the app.

Anki must be running with AnkiConnect installed before mining starts.

Tabs:
- **Single Episode**: mine one video/subtitle pair with file selectors and progress tracking.
- **Batch Processing**: queue multiple series for sequential processing.
- **YouTube**: paste a URL, fetch metadata, then mine.
- **Analytics**: history, series difficulty, milestones.
- **Settings**: Anki connection, media extraction, dictionary, word filtering. Saved to `~/.anki_miner/gui_config.json`.

## Recommended Setup

### Lapis Note Type

Anki Miner uses the [Lapis](https://github.com/donkuri/lapis) note type fields by default.

1. Download the latest `.apkg` from [Lapis releases](https://github.com/donkuri/lapis/releases).
2. In Anki: **File → Import** and select the `.apkg`.

Default field mapping:

| Anki Miner Field       | Note Field          | Content                       |
|------------------------|---------------------|-------------------------------|
| word                   | Expression          | Dictionary form of the word   |
| sentence               | Sentence            | Original subtitle line        |
| definition             | MainDefinition      | English definitions           |
| picture                | Picture             | Screenshot from the video     |
| audio                  | SentenceAudio       | Audio clip of the sentence    |
| expression_furigana    | ExpressionFurigana  | Word with furigana reading    |
| sentence_furigana      | SentenceFurigana    | Sentence with furigana reading|
| pitch_position         | *(unmapped)*        | Pitch accent position number  |
| pitch_category         | *(unmapped)*        | Pitch accent category         |
| frequency              | *(unmapped)*        | Word frequency rank           |

Fields marked *(unmapped)* have no default Lapis mapping. Map them in Settings if your note type has equivalents. Any note type with the required fields works.

### JMdict Offline Dictionary

For fast offline lookups:

```bash
mkdir -p ~/.anki_miner
wget -O ~/.anki_miner/JMdict_e.gz http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz
gunzip ~/.anki_miner/JMdict_e.gz
```

Without JMdict, lookups fall back to the Jisho API (slower, online, rate-limited).

## YouTube Mining

Paste a URL, click **Fetch Info** to probe metadata (title, duration, subtitle availability), then click **Mine**. The fetch downloads the video and its Japanese subtitle track into a per-run temporary directory, then passes both files to the same pipeline used for file-based mining.

Auto-captions are accepted only when native Japanese. Tracks that YouTube generates by machine-translating from English are rejected, since mining them yields unusable results. Native auto-captions remain lower quality than manual subtitles because they lack sentence boundaries.

Gotchas:

- **Bot-detection prompts**: if YouTube asks "Sign in to confirm you're not a bot", open **Settings → Cookies → Browser** and pick Firefox or Chrome. yt-dlp pulls cookies from that browser's profile on every fetch.
- **Age-restricted videos**: same fix.
- **Max duration**: defaults to 120 minutes. The probe aborts before downloading if the video is longer. Adjust in Settings.

## Troubleshooting

| Issue                    | Solution                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| "Cannot connect to Anki" | Start Anki and ensure AnkiConnect is installed.                                  |
| "Deck not found"         | Create the deck in Anki or update the deck name in Settings.                     |
| "Note type not found"    | Import Lapis (see above) or configure your own in Settings.                      |
| "ffmpeg not found"       | Install ffmpeg and add it to PATH.                                               |
| "JMdict file not found"  | Download to `~/.anki_miner/` (see above) or disable offline dictionary.          |
| Audio is wrong language  | The tool tries Japanese audio tracks first, then falls back to the default.     |
| Subtitles out of sync    | Use the subtitle offset control in the GUI.                                      |

## Issues and Contributing

Bug reports and feature ideas go in [Issues](https://github.com/0xzerolight/anki_miner/issues). See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
