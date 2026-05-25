# Anki Miner

[![CI](https://github.com/0xzerolight/anki_miner/actions/workflows/ci.yml/badge.svg)](https://github.com/0xzerolight/anki_miner/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/anki-miner.svg)](https://pypi.org/project/anki-miner/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Code of Conduct](https://img.shields.io/badge/code%20of%20conduct-Contributor%20Covenant%203.0-blueviolet.svg)](CODE_OF_CONDUCT.md)

Turn native Japanese content into Anki vocabulary cards - with screenshots, sentence audio, furigana, pitch accent, and frequency data.

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
- **Batch Mining**: queue a folder of episode/subtitle pairs for sequential processing.
- **YouTube**: paste one or more URLs, then mine the queue.
- **Analytics**: history, series difficulty rankings, milestones, undo.
- **Settings**: Anki, Media, Dictionary, Filtering, YouTube, Themes. Saved to `~/.anki_miner/gui_config.json`.

## Features

- Anki cards with furigana, pitch accent, and word frequency.
- **Bold the target word** in the sentence so it stands out on the card front.
- **Glossary field** that combines every enabled dictionary into one card field, compatible with the Senren dictionary-toggle template.
- Load any Yomitan dictionaries you like, reorder them, and add Jisho as an online fallback.
- YouTube queue: paste a list of URLs, mine the whole list in one click.
- Batch a folder of episode/subtitle pairs for unattended processing.
- Review and edit the word list before any cards are created.
- Audio in MP3 or Opus, at the bitrate you choose — Opus produces much smaller files for the same listening quality.
- Animated screenshots in AVIF or WebP for cards that show a moment of motion instead of a still frame.
- Analytics dashboard with history, undo, milestones, and series difficulty rankings.
- Four built-in themes (Light, Dark, Sakura, Tokyo Night) with a favorites list, `Ctrl+T` to cycle, and custom themes from a JSON file.

<details>
<summary><strong>How It Works</strong></summary>

1. **Read the subtitles** and split Japanese into individual words.
2. **Filter** to content words you don't already know.
3. **Grab a screenshot and audio clip** from the video for each line.
4. **Look up definitions** in your configured dictionaries, falling back to Jisho online if needed.
5. **Send the finished cards to Anki.**

</details>

## Dictionaries

Anki Miner looks up definitions through a **provider chain** you configure. Each lookup tries the providers in order; the first hit wins. Mix any number of offline Yomitan-format dictionaries with the Jisho online fallback, in any order.

Add a dictionary in **Settings → Add Dictionary…** by pointing at a Yomitan `.zip` archive. Drag entries to reorder the chain. Installed dictionaries are indexed once into `~/.anki_miner/dicts/<dict_id>/index.sqlite` and loaded on startup. Structured-content entries are rendered to HTML on import, so card definitions preserve the source dictionary's formatting (definition lists, examples, tags).

**Recommended Japanese → English dictionaries** — both are JMdict-derived; pick whichever fits your cards, or load both and order them as you like:

- **[Jitendex](https://github.com/Jitendex/Jitendex)** — modern JMdict successor with structured-content formatting, example sentences, and richer tags. Best for visually rich cards. Grab the Yomitan archive from the [Jitendex releases page](https://github.com/Jitendex/Jitendex/releases).
- **[JMdict](https://www.edrdg.org/jmdict/edict.html)** — the original community JMdict project. Plain-text glosses, smaller index, faster to add. Yomitan builds are available from the [Yomitan dictionary list](https://learnjapanese.moe/yomichan/#dictionaries) or you can rebuild from the EDRDG source.

Install via **Settings → Add Dictionary…** in either case.

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
| No definitions found     | Add a Yomitan dictionary in Settings → Add Dictionary…, or enable the Jisho fallback. |
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
