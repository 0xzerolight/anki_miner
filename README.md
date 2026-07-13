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
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (other) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Excludes local Whisper subtitle generation and AVIF screenshots. For full functionality: `pipx install "anki-miner[asr]"`.

<details>
<summary><strong>First-run notes (unsigned builds)</strong></summary>

- **macOS**: Gatekeeper blocks the app. Extract first, then `xattr -dr com.apple.quarantine AnkiMiner/`
- **Windows SmartScreen**: **More info** -> **Run anyway**.
- **Windows Defender false positive**: restore from **Protection history** or [report to Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

</details>

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

- **Video** - mine a single video/subtitle pair, a batch folder, or YouTube URLs.
- **Deck Builder** - mine a whole series into one frequency-ranked deck.
- **Audio** - mine audiobooks, podcasts, radio, songs (audio + subtitle/transcript pairs).
- **Reading** - mine manga (mokuro), novels (`.epub`, `.txt`), or standalone subtitle files.
- **Analytics** - mining history, difficulty rankings, milestones, undo.
- **Tools** - generate subtitles (local Whisper), retime subtitles (alass), condense media to dialogue-only audio.
- **Settings** - everything configurable.

## Other Features

- Extensive filtering: i+1, frequency limits, blacklist, regex, wordsets, and more.
- Offline Yomitan dictionary import - definitions, pitch accent, frequency - chained by priority.
- Multiple frequency lists chained by priority.
- Word audio on cards from local audio packs, JapanesePod101, or Google TTS.
- Per-dictionary glossary styling, Yomitan-style.
- Subtitle timing preview with adjustable offset.
- Animated screenshots (see example cards above).

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

## Roadmap

List of ideas for future versions of Anki Miner. Not in priority order. Feature requests take precedence.
- Suggest a feature - [Open an issue](https://github.com/0xzerolight/anki_miner/issues).
- Discuss the roadmap - [Discussions](https://github.com/0xzerolight/anki_miner/discussions).

- **Features**:
  - [x] UI language selection.
  - [x] Local subtitle creation tab: Opt-in tab to locally generate subtitles.
  - [x] Reading tab: Mine manga and books.
  - [ ] Media library: Expand Analytics tab to display local media library across all media forms.
  - [ ] Backfill tool.
  - [ ] Automatic subtitle downloading.

- **Long-term**:
  - [ ] Beyond Japanese: Mining other languages.
  - [ ] Android port.

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
