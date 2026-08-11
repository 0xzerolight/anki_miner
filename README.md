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

- **Anki** with the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on (code `2055492159`)
- **ffmpeg** + **libmpv** (video preview only) - needed only when installing via pip/pipx, `.deb`, or source.

Grab the download for your platform from the [latest release](https://github.com/0xzerolight/anki_miner/releases/latest):

| Platform | Download |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon / M1-M4) | `AnkiMiner-macOS-arm64.tar.gz` |
| macOS (Intel) | `AnkiMiner-macOS-x86_64.tar.gz` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` ² |
| Linux (other) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Excludes local Whisper subtitle generation and AVIF screenshots. For full functionality: `pipx install "anki-miner[asr]"`.

² Excludes local Whisper subtitle generation. Use the ASR-capable AppImage, or create a separate install with `pipx install "anki-miner[asr]"`.

### First-run notes (unsigned builds)

- **macOS**: Gatekeeper blocks the app. Extract first, then `xattr -dr com.apple.quarantine AnkiMiner/`
- **Windows SmartScreen**: **More info** -> **Run anyway**.
- **Windows Defender false positive**: restore from **Protection history** or [report to Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Install from PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

</details>

<details>
<summary><strong>Install from source</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

For full development setup, see [CONTRIBUTING.md](CONTRIBUTING.md).

</details>

## Tabs

- **Video** - mine a single video/subtitle pair, a batch folder, or YouTube URLs.
- **Deck Builder** - mine a whole series into one frequency-ranked deck.
- **Audiobooks** - mine audiobooks, podcasts, radio, songs (audio + subtitle/transcript pairs).
- **Reading** - mine manga (mokuro), novels (`.epub`, `.txt`; single book or a whole folder), standalone subtitle files, or pasted Japanese text.
- **Analytics** - mining history, difficulty rankings, milestones, undo.
- **Utilities** - generate subtitles (local Whisper), retime subtitles (alass), condense media to dialogue-only audio, and backfill fields on existing cards.
- **Settings** - everything configurable.

## Other Features

- Word Curator - review every candidate word before cards are made, with its scene, manga page, and dictionary entry side by side.
- Extensive filtering: i+1, frequency limits, blacklist, regex, wordsets, and more.
- Offline Yomitan dictionary import - definitions, pitch accent, frequency - chained by priority.
- Multiple frequency lists chained by priority.
- Word audio on cards from local audio packs, JapanesePod101, or Google TTS.
- Sentence audio on Reading cards from Google Translate TTS or Naver Papago (off by default).
- Per-dictionary glossary styling, Yomitan-style.
- Embedded libmpv video preview - play a word's scene while curating, or nudge subtitle timing with live playback.
- Animated screenshots (see example cards above).
- Settings profiles - save named configurations and switch between them from the header.
- Restyle Mined Cards - re-apply your current card styling to cards you already made (Tools menu).

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
2. **Filter** to content words you don't already know - optionally reviewing the list yourself in the Word Curator.
3. **Grab a screenshot and audio clip** from the video for each line.
4. **Look up definitions** in your configured offline dictionaries, optionally falling back to Jisho online if enabled (slower, rate-limited).
5. **Send the finished cards to Anki.**

</details>

## Recommended Resources

| Type | Resource | Download | Add via |
|------|----------|----------|---------|
| Dictionary | [JMdict](https://github.com/yomidevs/jmdict-yomitan) | [Yomitan zip](https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip) | Add Dictionary… |
| Dictionary | [Jitendex](https://jitendex.org/) | [Yomitan zip](https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip) | Add Dictionary… |
| Dictionary | [Bee's Character Dictionary](https://characterdictionary.tokyo/) | Generated on site | Add Dictionary… |
| Pitch | [Kanjium](https://github.com/mifunetoshiro/kanjium) | [TSV](https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt) | Pitch Accent -> Add pitch source… |
| Pitch | [アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries) | [Drive](https://drive.google.com/drive/folders/1tTdLppnqMfVC5otPlX_cs4ixlIgjv_lH) | Pitch Accent -> Add pitch source… |
| Frequency | [JPDB v2.2 Kana](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13.zip) | Frequency -> Add frequency source… |
| Frequency | [BCCWJ SUW+LUW](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/BCCWJ_SUW_LUW_combined.zip) | Frequency -> Add frequency source… |


<details>
<summary><strong>JMnedict License</strong></summary>

Uses bundled name wordsets derived from [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (JMdict/EDICT project, EDRDG, CC BY-SA 4.0).

</details>

## Troubleshooting

| Issue                    | Solution                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| "Cannot connect to Anki" | Start Anki and ensure AnkiConnect is installed.                                  |
| "Deck not found"         | Pick an existing deck in Settings -> Cards & Anki. Decks are not created for you; make it in Anki first if you need a new one. |
| "Note type not found"    | Configure your note type's field names in Settings -> Cards & Anki.               |
| "ffmpeg not found"       | Install ffmpeg and add it to PATH.                                               |
| No definitions found     | Add a Yomitan dictionary in Settings -> Add Dictionary… (recommended), or enable the Jisho fallback (slower, rate-limited). |
| Windows installer will not open / SmartScreen warning | See [First-run notes](#first-run-notes-unsigned-builds): select **More info** -> **Run anyway**; restore Defender false positives from **Protection history**. |
| Fresh install has no definitions | Run Tools -> Setup Wizard or Tools -> Download Recommended Resources. For manual import, keep the Yomitan ZIP intact (do not unzip it). |
| Add Dictionary stalls or fails | Note the last visible stage and attach logs (see "Where are the logs?" below). Include the dictionary ZIP name, source, and size in the report. |
| Where are the logs?      | Use Help -> Open Log Folder, or open `%USERPROFILE%\.anki_miner\anki_miner.log` on Windows or `~/.anki_miner/anki_miner.log` on macOS/Linux. Rotated logs use the `.1` through `.5` suffixes. |
| Reporting a bug          | Help → Export Diagnostics… writes a ZIP with logs and system details to a location you choose. Review it before uploading because it contains file paths and file names from your computer. Nothing is uploaded automatically. |
| More diagnostic logging | Set `ANKI_MINER_LOG_LEVEL=DEBUG` before starting Anki Miner to capture third-party yt-dlp, urllib3, and fugashi details. The default is `WARNING`; Anki Miner logs remain at DEBUG. |
| Audio is wrong language  | The tool tries Japanese audio tracks first, then falls back to the default.      |
| Subtitles out of sync    | Use the subtitle offset control in the GUI (range ±300 seconds).                 |

## Roadmap

List of ideas for future versions of Anki Miner. Not in priority order. Feature requests take precedence.
- Suggest a feature - [Open an issue](https://github.com/0xzerolight/anki_miner/issues).
- Discuss the roadmap - [Discussions](https://github.com/0xzerolight/anki_miner/discussions).

- **Features**:
  - [x] UI language selection.
  - [x] Local subtitle creation tab: Opt-in tab to locally generate subtitles.
  - [x] Reading tab: Mine manga and books.
  - [x] Backfill tool.
  - [ ] Media library: Expand Analytics tab to display local media library across all media forms.
  - [ ] Automatic subtitle downloading.

- **Long-term**:
  - [x] Android port -- https://github.com/0xzerolight/anki_miner_android
  - [ ] Beyond Japanese: Mining other languages.
  - [ ] Anki Miner browser extension.


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
