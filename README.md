<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="PyPI version"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="GitHub downloads"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="GitHub stars"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord community"></a>
</p>

<p align="center">
Turn native Japanese content into Anki vocabulary cards.
</p>

<p align="center">
Please leave a ⭐ star if Anki Miner helped you - it helps others find it :).
</p>


# <p align="center">Mining Demo</p>

![Anki Miner Showcase](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Full demo with sound (MP4)</a></p>

### Example cards

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Installation

### Requirements

- **ffmpeg** **only if installing from pip/pipx, .deb, or from source.**
- **alass** (optional) for automatic subtitle retiming. Linux and Windows release builds bundle it. macOS users: `brew install alass` or place it on PATH.
- **Anki** with the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on. In Anki: **Tools -> Add-ons -> Get Add-ons**, paste code `2055492159`, restart.

### Download

Grab the download for your platform from the [latest release](https://github.com/0xzerolight/anki_miner/releases/latest):

| Platform | Download |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon / M1-M4) | `AnkiMiner-macOS-arm64.tar.gz` |
| macOS (Intel) | `AnkiMiner-macOS-x86_64.tar.gz` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (other) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ The Intel macOS build excludes local Whisper speech-to-text (Subtitles->Generate) and AVIF animated screenshots - every other feature works. For full functionality on an Intel Mac, install via pip instead: `pipx install "anki-miner[asr]"`. 

**macOS first-run (unsigned binary):** macOS Gatekeeper will block the app because it is not notarised. Extract the archive first, then clear the quarantine flag on the extracted folder (clearing it on the `.tar.gz` does not carry over to the extracted files):
```bash
xattr -dr com.apple.quarantine AnkiMiner/
```

**Windows first-run (SmartScreen):** Windows SmartScreen may show "Windows protected your PC". Click **More info**, then **Run anyway**.

**Windows Defender false positive:** Defender may wrongly flag the unsigned `.exe` (it bundles `yt-dlp`/`ffmpeg`, common AV triggers). Restore it from **Protection history** or [report it to Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Install from PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
```

</details>

<details>
<summary><strong>Install from source</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
```

For full development setup, see [CONTRIBUTING.md](CONTRIBUTING.md).

</details>

## Tabs

- **Episode Mining**: mine one video/subtitle pair with word curation.
- **Batch Mining**: batch mine a folder of episode/subtitle pairs for sequential processing. Files are paired by episode number, so each folder / queue item should hold a single show (use Multi-Series Queue for mining multiple series at a time).
- **Deck Builder**: point at a folder of episode/subtitle pairs and mine the full series into one named deck. Ranked by frequency; pick how many to include (all, top N, or a coverage target) and preview before cards are created.
- **YouTube**: paste one or more URLs, then mine the queue.
- **Audio**: queue local audio + subtitle/transcript pairs (audiobooks, podcasts, radio, songs, lectures) and mine them audio-only; embedded cover art stands in for screenshots.
- **Reading**: mine manga and novels instead of video. Point at a mokuro-processed manga volume (an image folder or `.cbz` with its sibling `.mokuro` file) or a novel (`.epub`, or Aozora/plain `.txt`); cards carry the page image or the book cover. Anki Miner reads mokuro's output and does no OCR itself. Word curation and preview work as in the other tabs.
- **Analytics**: history, series difficulty rankings, milestones, undo.
- **Subtitles**: generate subtitles from speech with a local Whisper model (no GPU required; optional CUDA/VAD packs install in-app), or retime an out-of-sync subtitle file to your video with alass.
- **Settings**: Anki, Media, Dictionaries, Audio, Filtering, Frequency, Subtitles, YouTube, Themes. Saved to `~/.anki_miner/gui_config.json`.

## Other Features

- Extensive filtering options (i+1 filter, frequency limits, word blacklist, subtitle regex filtering, wordset filtering, per-volume minimum word occurrence, and more).
- Offline Yomitan dictionary import (definitions, pitch accent, frequency data) with priority ordering.
- Multiple frequency lists chained together, each indexed separately and ordered by priority.
- Expression (word-level) audio on cards from local audio packs, JapanesePod101, or Google Translate TTS (opt-in, chained).
- Definition styling presets (like Yomitan) or custom CSS.
- Subtitle timing preview with adjustable offset.
- Animated screenshots (see example card gifs).

<details>
<summary><strong>Built-in themes (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (light); Frappé, Macchiato, Mocha (dark)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (light), Wave (dark)
- **Rosé Pine** - Dawn (light); Main, Moon (dark)
- **Solarized** - Light, Dark
- **Standalone** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Theme licenses: [LICENSE-THEMES.md](LICENSE-THEMES.md). 
Want another theme added? Suggest in a GitHub Issue.

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

| Type | Resource | What you get | Download | Add via |
|------|----------|--------------|----------|---------|
| Dictionary | [Jitendex](https://jitendex.org/) | JMdict successor; structured formatting, examples, tags | [Yomitan zip](https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip) | Add Dictionary… |
| Dictionary | [JMdict](https://github.com/yomidevs/jmdict-yomitan) | Plain glosses; smaller, faster to index | [Yomitan zip](https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip) | Add Dictionary… |
| Dictionary | [Bee's Character Dictionary](https://characterdictionary.tokyo/) | Character names from your AniList/VNDB lists, with roles and descriptions | Generated on site | Add Dictionary… |
| Pitch | [Kanjium](https://github.com/mifunetoshiro/kanjium) | ~124k patterns; drop-in TSV, no import step | [TSV](https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt) | Dictionary -> Pitch Accent File |
| Pitch | [アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries) | Richer NHK notation | [Drive](https://drive.google.com/drive/folders/1tTdLppnqMfVC5otPlX_cs4ixlIgjv_lH) | Dictionary -> Pitch Accent File |
| Frequency | [JPDB v2.2 Kana](https://github.com/Kuuuube/yomitan-dictionaries) | All-round default for media | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13.zip) | Filtering -> Frequency List File |
| Frequency | [BCCWJ SUW+LUW](https://github.com/Kuuuube/yomitan-dictionaries) | Balanced corpus; pairs well with news/novels | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/BCCWJ_SUW_LUW_combined.zip) | Filtering -> Frequency List File |


Dictionaries are indexed once into `~/.anki_miner/dicts/` (drag to reorder the chain).
The pitch and frequency pickers accept a raw CSV/TSV or a Yomitan zip, auto-converted to `~/.anki_miner/pitch_accent.csv` / `frequency.csv` on Save.
[Bee's Character Dictionary](https://characterdictionary.tokyo/) builds a custom Yomitan dictionary from your AniList/VNDB media lists, so character names in the shows you mine resolve to real definitions; re-generate and re-import when your lists change.

Proper-noun filtering uses bundled name wordsets derived from [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (JMdict/EDICT project, EDRDG, CC BY-SA 4.0).

## Troubleshooting

| Issue                    | Solution                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| "Cannot connect to Anki" | Start Anki and ensure AnkiConnect is installed.                                  |
| "Deck not found"         | The deck is created automatically when mining starts; if you meant a different deck, update the name in Settings. |
| "Note type not found"    | Configure your note type's field names in Settings -> Anki.                       |
| "ffmpeg not found"       | Install ffmpeg and add it to PATH.                                               |
| No definitions found     | Add a Yomitan dictionary in Settings -> Add Dictionary… (recommended), or enable the Jisho fallback (slower, rate-limited). |
| Audio is wrong language  | The tool tries Japanese audio tracks first, then falls back to the default.      |
| Subtitles out of sync    | Use the subtitle offset control in the GUI (range ±300 seconds).                 |
| AV1 won't preview        | In-app AV1 preview needs a hardware AV1 decoder (RTX-30+/Tiger-Lake+). Without one, the pane shows an "AV1 can't be decoded for preview" notice. Mining is unaffected - screenshots are extracted by FFmpeg, not the preview. |

## Roadmap

List of ideas for future versions of Anki Miner. Not in priority order. Feature requests take precedence.
- Suggest a feature - [Open an issue](https://github.com/0xzerolight/anki_miner/issues).
- Discuss the roadmap - [Discussions](https://github.com/0xzerolight/anki_miner/discussions).

- **Features**:
  - [x] UI language selection.
  - [x] Local subtitle creation tab: Opt-in tab to locally generate subtitles.
  - [x] Reading tab: Mine manga and books.
  - [ ] Media library: Expand Analytics tab to display local media library across all media forms.

- **Improvements**:
  - [x] Improved user onboarding: automatic recommended resource fetching, easier setup.

- **Researching/Under consideration**:
  - [ ] Android port.
  - [ ] Jellyfin integration.
  - [ ] Jimaku integration.

- **Long-term**:
  - [ ] Beyond Japanese: Mining other languages.

## Contributing

Contributions of any kind are welcome.
If you want to support the project, please share it with others who may benefit from it.

- New here? Start with [CONTRIBUTING.md](CONTRIBUTING.md).
- Architecture overview: [ARCHITECTURE.md](ARCHITECTURE.md).
- Testing strategy: [TESTING.md](TESTING.md).
- Code of Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- Security: [SECURITY.md](SECURITY.md).

Bug reports and feature requests -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
General questions and discussion -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) or [Discord](https://discord.com/invite/aDtQyZzUVP).

## Special Thanks

Sincere thanks to people who made exceptional contributions to the project:

★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Brilliant feature suggestions, new release testing, community building

See [CONTRIBUTORS.md](CONTRIBUTORS.md) for everyone who has made any kind of contribution to the project.


## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
