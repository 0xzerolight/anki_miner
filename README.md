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

<!-- i18n-nav:start -->
<p align="center">
<b>English</b> ·
<a href="i18n/README.ja.md">日本語</a> ·
<a href="i18n/README.ru.md">Русский</a> ·
<a href="i18n/README.fr.md">Français</a> ·
<a href="i18n/README.es.md">Español</a> ·
<a href="i18n/README.de.md">Deutsch</a> ·
<a href="i18n/README.pt_br.md">Português (Brasil)</a> ·
<a href="i18n/README.id.md">Bahasa Indonesia</a> ·
<a href="i18n/README.vi.md">Tiếng Việt</a> ·
<a href="i18n/README.zh_cn.md">简体中文</a> ·
<a href="i18n/README.zh_tw.md">繁體中文</a> ·
<a href="i18n/README.it.md">Italiano</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Turn native content in Japanese, Chinese, Korean, and twenty-nine more languages into Anki vocabulary cards.
</p>

<p align="center">
Also on Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner for Android</a>.
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
- **ffmpeg** + **libmpv** (video preview only) - needed only when installing via pip/pipx or source.

Grab the download for your platform from the [latest release](https://github.com/0xzerolight/anki_miner/releases/latest):

| Platform | Download |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (other) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Excludes AVIF screenshots and silence removal for generated subtitles.

### First-run notes (unsigned builds)

- **macOS**: open the `.dmg`, drag **AnkiMiner** onto **Applications**, then launch it. The first launch is blocked because the app is not notarized: dismiss the dialog, open the app again, and choose **Open Anyway** (also under **System Settings** -> **Privacy & Security**). Once only.
- **Linux AppImage**: make it executable before running it - `chmod +x AnkiMiner-*.AppImage`, or **Properties** -> **Allow executing** in your file manager.
- **Windows SmartScreen**: **More info** -> **Run anyway**.
- **Windows Defender false positive**: restore from **Protection history** or [report to Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Install from PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Japanese, Indonesian and Hebrew need nothing extra. For another mining language, add its engine:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Arabic and Persian have no extra. Their data, and the spaCy models of the European languages, download in-app from Settings -> Mining Language, where the downloads above fetch everything.

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

- **Video** - mine a single video/subtitle pair, a batch folder, YouTube URLs, or a whole series into one frequency-ranked deck.
- **Audiobooks** - mine audiobooks, podcasts, radio, songs (audio + subtitle/transcript pairs).
- **Reading** - mine manga (mokuro), novels (`.epub`, `.txt`; single book or a whole folder), standalone subtitle files, or pasted text.
- **Analytics** - mining history, difficulty rankings, milestones.
- **Utilities** - generate subtitles (local Whisper), retime subtitles (ffsubsync/alass), condense media to dialogue-only audio, download video/audio/subtitles from any site yt-dlp supports, copy the worth-learning part of a premade deck into a new one, backfill fields on existing cards, OCR manga page images into .mokuro files (mokuro), and time an audiobook to its book's text (Audiobook Sync).
- **Settings** - everything configurable.

## Other Features

- Mining languages - Japanese, Chinese, Korean, English, Catalan, German, Portuguese, French, Spanish, Italian, Dutch, Norwegian Bokmål, Romanian, Greek, Finnish, Hungarian, Croatian, Swedish, Polish, Lithuanian, Danish, Turkish, Indonesian, Russian, Arabic, Thai, Persian, Slovenian, Ukrainian, Vietnamese, Cantonese, and Hebrew, switched in Settings. Languages other than Japanese download their engine in-app; Indonesian and Hebrew need none.
- Word Curator - review every candidate word before cards are made, with its scene, manga page, and dictionary entry side by side.
- Undo a run - delete the notes a run just created, straight from its results dialog.
- Extensive filtering: i+1, frequency rank range, blacklist, regex, wordsets, and more.
- Offline Yomitan dictionary import - definitions, pitch accent, frequency - chained by priority.
- Multiple frequency lists chained by priority.
- Word audio on cards from local audio packs, JapanesePod101, Google TTS, or Microsoft Edge TTS.
- Sentence audio on Reading cards from Google Translate TTS, or Naver Papago for Japanese and Korean (off by default).
- Per-dictionary glossary styling, Yomitan-style.
- Embedded libmpv video preview - play a word's scene while curating, or nudge subtitle timing with live playback.
- Animated screenshots (see example cards above).
- Settings profiles - save named configurations and switch between them from the header.
- Restyle Mined Cards - re-apply your current card styling to cards you already made (Utilities → Card Backfill).

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

1. **Read the subtitles** and split the text into individual words.
2. **Filter** to content words you don't already know - optionally reviewing the list yourself in the Word Curator.
3. **Grab a screenshot and audio clip** from the video for each line.
4. **Look up definitions** in your configured offline dictionaries, optionally falling back to Jisho online for Japanese (slower, rate-limited).
5. **Send the finished cards to Anki.**

</details>

## Recommended Resources

Dictionaries and frequency lists for every mining language, with download links: [RESOURCES.md](RESOURCES.md). The Setup Wizard offers the right set for your mining language.

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
| No definitions found     | Add a Yomitan dictionary in Settings -> Dictionaries -> Add dictionary… (recommended), or, for Japanese, enable the Jisho fallback (slower, rate-limited). |
| Windows installer will not open / SmartScreen warning | See [First-run notes](#first-run-notes-unsigned-builds): select **More info** -> **Run anyway**; restore Defender false positives from **Protection history**. |
| Fresh install has no definitions | Run Tools -> Setup Wizard or Tools -> Download Recommended Resources. For manual import, keep the Yomitan ZIP intact (do not unzip it). |
| Add Dictionary stalls or fails | Note the last visible stage and attach logs (see "Where are the logs?" below). Include the dictionary ZIP name, source, and size in the report. |
| Where are the logs?      | Use Help -> Open Log Folder, or open `%USERPROFILE%\.anki_miner\anki_miner.log` on Windows or `~/.anki_miner/anki_miner.log` on macOS/Linux. Rotated logs use the `.1` through `.5` suffixes. Send `anki_miner.crash` too if it is there (a crash that took the app down writes its stack to that file, not to the log), and `anki_miner.child.log`, which holds a helper process's output. |
| Reporting a bug          | Help -> Export Diagnostics… writes a ZIP to a location you choose, holding the logs (`anki_miner.log` and its rotations, `anki_miner.crash`, `anki_miner.child.log`), your `settings.json`, your config and UI-state files, queue snapshots and download manifests, and generated reports of the machine and app state (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Review it before uploading because it contains file paths and file names from your computer. Nothing is uploaded automatically. |
| More diagnostic logging | Set `ANKI_MINER_LOG_LEVEL=DEBUG` before starting Anki Miner to capture third-party yt-dlp, urllib3, and fugashi details. The default is `WARNING`; Anki Miner logs remain at DEBUG. |
| Audio is wrong language  | The tool picks the mining language's audio track, else the first one. Choose it yourself with Tracks (Video -> Single). |
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
  - [x] Android port - https://github.com/0xzerolight/anki_miner_android
  - [x] Beyond Japanese: thirty-one more mining languages.
  - [ ] Anki Miner browser extension.


## Contributing

Contributions of any kind are welcome.
If you want to support the project, please share it with others who may benefit from it.

- New here? Start with [CONTRIBUTING.md](CONTRIBUTING.md).
- Architecture overview: [ARCHITECTURE.md](ARCHITECTURE.md).
- Code of Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- Security: [SECURITY.md](SECURITY.md).

Bug reports and feature requests -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
General questions and discussion -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) or [Discord](https://discord.com/invite/aDtQyZzUVP).

## Special Thanks

Sincere thanks to people who made exceptional contributions to the project:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Brilliant feature suggestions, new release testing, community building.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Excellent feature suggestions, community building and moderation on Discord.

See [CONTRIBUTORS.md](CONTRIBUTORS.md) for everyone who has made any kind of contribution to the project.


## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
