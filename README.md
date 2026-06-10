# <p align="center">Anki Miner</p>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="PyPI version"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg?cacheSeconds=3600" alt="GitHub downloads"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social&cacheSeconds=3600" alt="GitHub stars"></a>
</p>

<p align="center">
Turn native Japanese content into Anki vocabulary cards - with screenshots, sentence audio, furigana, pitch accent, and frequency data.
</p>

<p align="center">
Please leave a ⭐ star if Anki Miner helped you - it helps others find it.
</p>


## Showcase

![Anki Miner Showcase](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Full demo with sound (MP4)</a></p>

### Example cards

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Installation

### Requirements

- **ffmpeg** — bundled with the standalone builds (Windows `.exe`/`.zip`, macOS tarball, Linux AppImage/tarball), so most users need nothing. You only need ffmpeg on PATH if you install via **PyPI/`pipx`, from source, or the `.deb`** (which ships without bundled ffmpeg for licensing reasons and uses your system copy).
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
- Review and edit the word list before any cards are created — in single-episode mining, and as an opt-in popup for batch runs too.
- **Word filtering**: skip words already in your collection, cap by frequency rank, exclude proper nouns via bundled name wordsets, and optionally drop hiragana-only or katakana-only words.
- **Text size control**: scale the whole UI from 0.5× to 2× under Settings → Themes.
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

## Recommended Resources

None of these ship with Anki Miner — load the ones you want, all free. Definitions are looked up through a **provider chain** (first hit wins); Jisho is a slower, rate-limited online fallback, off by default.

| Type | Resource | What you get | Download | Add via |
|------|----------|--------------|----------|---------|
| Dictionary | [Jitendex](https://jitendex.org/) | JMdict successor; structured formatting, examples, tags | [Yomitan zip](https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip) | Add Dictionary… |
| Dictionary | [JMdict](https://github.com/yomidevs/jmdict-yomitan) | Plain glosses; smaller, faster to index | [Yomitan zip](https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip) | Add Dictionary… |
| Pitch | [Kanjium](https://github.com/mifunetoshiro/kanjium) | ~124k patterns; drop-in TSV, no import step | [TSV](https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt) | Dictionary → Pitch Accent File |
| Pitch | [アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries) | Richer NHK notation | [Drive](https://drive.google.com/drive/folders/1tTdLppnqMfVC5otPlX_cs4ixlIgjv_lH) | Dictionary → Pitch Accent File |
| Frequency | [JPDB v2.2 Kana](https://github.com/Kuuuube/yomitan-dictionaries) | All-round default for media | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13.zip) | Filtering → Frequency List File |
| Frequency | [BCCWJ SUW+LUW](https://github.com/Kuuuube/yomitan-dictionaries) | Balanced corpus; pairs well with news/novels | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/BCCWJ_SUW_LUW_combined.zip) | Filtering → Frequency List File |

Dictionaries are indexed once into `~/.anki_miner/dicts/` (drag to reorder the chain).
The pitch and frequency pickers accept a raw CSV/TSV or a Yomitan zip, auto-converted to `~/.anki_miner/pitch_accent.csv` / `frequency.csv` on Save.

Proper-noun filtering uses bundled name wordsets derived from [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (JMdict/EDICT project, EDRDG, CC BY-SA 4.0).

## YouTube Mining

Paste one or more URLs into the YouTube tab. Each row shows its title, length, and subtitle source as you add it; click **Mine** to process the whole list. Transient download errors are retried once before a row is marked failed. Cancel is safe at any point.

Playlist URLs (`/playlist?list=…`) expand into individual queue rows automatically. Watch URLs that carry a `list=` parameter (`/watch?v=…&list=…`) prompt whether to add just that video or the whole playlist. The maximum number of videos pulled from a single playlist defaults to 100 and is configurable under Settings → YouTube. Mix/radio playlist URLs (`list=RD…`) are treated as plain video links. Videos already in the queue are skipped.

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
| Subtitles out of sync    | Use the subtitle offset control in the GUI (range ±300 seconds).                 |
| AV1 video won't preview  | In-app preview is disabled for AV1 to avoid decoder error spam. Mining still works normally — only the preview is skipped. |

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
