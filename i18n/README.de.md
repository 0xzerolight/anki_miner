<!-- i18n-source: README.md sha256:3faba83f26c9d1af -->

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
<a href="../README.md">English</a> ·
<a href="README.ja.md">日本語</a> ·
<a href="README.ru.md">Русский</a> ·
<a href="README.fr.md">Français</a> ·
<a href="README.es.md">Español</a> ·
<b>Deutsch</b> ·
<a href="README.pt_br.md">Português (Brasil)</a> ·
<a href="README.id.md">Bahasa Indonesia</a> ·
<a href="README.vi.md">Tiếng Việt</a> ·
<a href="README.zh_cn.md">简体中文</a> ·
<a href="README.zh_tw.md">繁體中文</a> ·
<a href="README.it.md">Italiano</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Wandelt originalsprachige japanische, chinesische und koreanische Inhalte in Anki-Vokabelkarten um.
</p>

<p align="center">
Auch auf Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner für Android</a>.
</p>

<p align="center">
Bitte hinterlasse einen ⭐ Stern, wenn dir Anki Miner geholfen hat - das hilft anderen, es zu finden :).
</p>


# <p align="center">Mining-Demo</p>

![Anki Miner Showcase](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Vollständige Demo mit Ton (MP4)</a></p>

### Beispielkarten

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Installation

### Voraussetzungen

- **Anki** mit dem [AnkiConnect](https://ankiweb.net/shared/info/2055492159)-Add-on (Code `2055492159`)
- **ffmpeg** + **libmpv** (nur für die Videovorschau) - nur bei Installation über pip/pipx oder aus dem Quellcode nötig.

Lade den Download für deine Plattform von der [neuesten Version](https://github.com/0xzerolight/anki_miner/releases/latest) herunter:

| Plattform | Download |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon / M1-M4) | `AnkiMiner-macOS-arm64.tar.gz` |
| macOS (Intel) | `AnkiMiner-macOS-x86_64.tar.gz` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (sonstige) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Ohne lokale Whisper-Untertitelgenerierung und AVIF-Screenshots. Für vollen Funktionsumfang: `pipx install "anki-miner[asr]"`.

### Hinweise zum ersten Start (unsignierte Builds)

- **macOS**: Gatekeeper blockiert die App. Zuerst entpacken, dann `xattr -dr com.apple.quarantine AnkiMiner/`
- **Windows SmartScreen**: **Weitere Informationen** -> **Trotzdem ausführen**.
- **Windows Defender Fehlalarm**: aus dem **Schutzverlauf** wiederherstellen oder [an Microsoft melden](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Installation über PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

</details>

<details>
<summary><strong>Installation aus dem Quellcode</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Die vollständige Entwicklungseinrichtung findest du in [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Tabs

- **Video** - mine ein einzelnes Video/Untertitel-Paar, einen Stapelordner oder YouTube-URLs.
- **Deck Builder** - mine eine ganze Serie zu einem nach Häufigkeit geordneten Stapel.
- **Audiobooks** - mine Hörbücher, Podcasts, Radio, Songs (Audio- + Untertitel-/Transkript-Paare).
- **Reading** - mine Manga (mokuro), Romane (`.epub`, `.txt`; einzelnes Buch oder ein ganzer Ordner), eigenständige Untertiteldateien oder eingefügten Text.
- **Analytics** - Mining-Verlauf, Schwierigkeitsrangliste, Meilensteine.
- **Utilities** - Untertitel erzeugen (lokales Whisper), Untertitel neu timen (ffsubsync/alass), Medien auf reines Dialog-Audio kondensieren, den lernenswerten Teil eines fertigen Stapels in einen neuen kopieren und Felder bestehender Karten nachträglich befüllen.
- **Settings** - alles konfigurierbar.

## Weitere Funktionen

- Mining-Sprachen - Japanisch, Chinesisch und Koreanisch, umschaltbar in den Einstellungen. Koreanisch lädt sein Sprachmodell in der App herunter.
- Word Curator - jedes Kandidatenwort vor der Kartenerstellung prüfen, mit Szene, Manga-Seite und Wörterbucheintrag nebeneinander.
- Lauf rückgängig machen - die Notizen, die ein Lauf gerade erstellt hat, direkt aus seinem Ergebnisdialog löschen.
- Umfangreiche Filterung: i+1, Häufigkeitsrang-Bereich, Sperrliste, Regex, Wortgruppen und mehr.
- Offline-Import von Yomitan-Wörterbüchern - Definitionen, Tonhöhenakzent, Häufigkeit - nach Priorität verkettet.
- Mehrere Häufigkeitslisten, nach Priorität verkettet.
- Wortaudio auf Karten aus lokalen Audio-Paketen, JapanesePod101 oder Google TTS.
- Satzaudio auf Reading-Karten von Google Translate TTS oder Naver Papago (standardmäßig aus).
- Wörterbuchspezifisches Glossar-Styling im Yomitan-Stil.
- Eingebettete libmpv-Videovorschau - die Szene eines Worts während der Prüfung abspielen oder das Untertitel-Timing per Live-Wiedergabe nachjustieren.
- Animierte Screenshots (siehe Beispielkarten oben).
- Einstellungsprofile - benannte Konfigurationen speichern und über den Header wechseln.
- Gesammelte Karten neu gestalten - dein aktuelles Karten-Styling auf bereits erstellte Karten anwenden (Werkzeuge-Menü).

<details>
<summary><strong>Integrierte Themes (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (hell); Frappé, Macchiato, Mocha (dunkel)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (hell), Wave (dunkel)
- **Rosé Pine** - Dawn (hell); Main, Moon (dunkel)
- **Solarized** - Light, Dark
- **Standalone** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Theme-Lizenzen: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Möchtest du ein weiteres Theme vorschlagen? Reiche einen Vorschlag als GitHub Issue ein.

</details>

<details>
<summary><strong>So funktioniert es</strong></summary>

1. **Untertitel einlesen** und den Text in einzelne Wörter zerlegen.
2. **Filtern** auf Inhaltswörter, die du noch nicht kennst - optional selbst im Word Curator prüfen.
3. **Screenshot und Audioclip** für jede Zeile aus dem Video holen.
4. **Definitionen nachschlagen** in deinen konfigurierten Offline-Wörterbüchern, optional mit Rückgriff auf Jisho online, falls aktiviert (langsamer, ratenbegrenzt).
5. **Fertige Karten an Anki senden.**

</details>

## Empfohlene Ressourcen

| Typ | Ressource | Download | Hinzufügen über |
|------|----------|----------|---------|
| Wörterbuch | [JMdict](https://github.com/yomidevs/jmdict-yomitan) | [Yomitan-ZIP](https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip) | Wörterbuch hinzufügen… |
| Wörterbuch | [Jitendex](https://jitendex.org/) | [Yomitan-ZIP](https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip) | Wörterbuch hinzufügen… |
| Wörterbuch | [Bee's Character Dictionary](https://characterdictionary.tokyo/) | Auf der Website generiert | Wörterbuch hinzufügen… |
| Tonhöhenakzent | [Kanjium](https://github.com/mifunetoshiro/kanjium) | [TSV](https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt) | Tonhöhenakzent -> Tonhöhenquelle hinzufügen… |
| Tonhöhenakzent | [アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries) | [Drive](https://drive.google.com/drive/folders/1tTdLppnqMfVC5otPlX_cs4ixlIgjv_lH) | Tonhöhenakzent -> Tonhöhenquelle hinzufügen… |
| Häufigkeit | [JPDB v2.2 Kana](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan-ZIP](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13.zip) | Häufigkeit -> Häufigkeitsquelle hinzufügen… |
| Häufigkeit | [BCCWJ SUW+LUW](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan-ZIP](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/BCCWJ_SUW_LUW_combined.zip) | Häufigkeit -> Häufigkeitsquelle hinzufügen… |
| Wortaudio | [local-audio-yomichan](https://github.com/yomidevs/local-audio-yomichan) | Sammlungs-Torrent oder generierte `android.db` | Audio -> Audioquelle hinzufügen… |


<details>
<summary><strong>JMnedict-Lizenz</strong></summary>

Verwendet mitgelieferte Namens-Wortgruppen, abgeleitet von [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (JMdict/EDICT-Projekt, EDRDG, CC BY-SA 4.0).

</details>

## Fehlerbehebung

| Problem                    | Lösung                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| „Kann keine Verbindung zu Anki herstellen“ | Anki starten und sicherstellen, dass AnkiConnect installiert ist.  |
| „Stapel nicht gefunden“         | Einen vorhandenen Stapel in Einstellungen -> Karten & Anki auswählen. Stapel werden nicht automatisch erstellt; lege ihn bei Bedarf zuerst in Anki an. |
| „Notiztyp nicht gefunden“    | Die Feldnamen deines Notiztyps in Einstellungen -> Karten & Anki konfigurieren. |
| „ffmpeg nicht gefunden“       | ffmpeg installieren und zum PATH hinzufügen.                                     |
| Keine Definitionen gefunden     | Ein Yomitan-Wörterbuch unter Einstellungen -> Wörterbuch hinzufügen… ergänzen (empfohlen) oder den Jisho-Rückgriff aktivieren (langsamer, ratenbegrenzt). |
| Windows-Installer öffnet nicht / SmartScreen-Warnung | Siehe [Hinweise zum ersten Start](#hinweise-zum-ersten-start-unsignierte-builds): **Weitere Informationen** -> **Trotzdem ausführen** wählen; Defender-Fehlalarme aus dem **Schutzverlauf** wiederherstellen. |
| Frische Installation hat keine Definitionen | Tools -> Einrichtungsassistent oder Tools -> Empfohlene Ressourcen herunterladen ausführen. Für den manuellen Import die Yomitan-ZIP unverändert lassen (nicht entpacken). |
| Wörterbuch hinzufügen bleibt hängen oder schlägt fehl | Die zuletzt sichtbare Phase notieren und Logs anhängen (siehe „Wo sind die Logs?“ unten). Name, Quelle und Größe der Wörterbuch-ZIP in der Meldung angeben. |
| Wo sind die Logs?      | Hilfe -> Protokollordner öffnen verwenden, oder unter Windows `%USERPROFILE%\.anki_miner\anki_miner.log` bzw. unter macOS/Linux `~/.anki_miner/anki_miner.log` öffnen. Rotierte Logs verwenden die Endungen `.1` bis `.5`. |
| Einen Fehler melden          | Hilfe -> Diagnose exportieren… schreibt eine ZIP mit Logs und Systemdetails an einen Ort deiner Wahl. Vor dem Hochladen prüfen, da sie Dateipfade und Dateinamen von deinem Computer enthält. Es wird nichts automatisch hochgeladen. |
| Mehr Diagnoseprotokollierung | `ANKI_MINER_LOG_LEVEL=DEBUG` vor dem Start von Anki Miner setzen, um Details von yt-dlp, urllib3 und fugashi (Drittanbieter) zu erfassen. Standard ist `WARNING`; Anki-Miner-Logs bleiben bei DEBUG. |
| Audio ist in falscher Sprache  | Das Tool versucht zuerst Audiospuren in der Mining-Sprache, dann greift es auf die Standardspur zurück. |
| Untertitel sind nicht synchron    | Die Untertitel-Offset-Steuerung in der GUI verwenden (Bereich ±300 Sekunden).      |

## Roadmap

Liste von Ideen für künftige Versionen von Anki Miner. Nicht nach Priorität geordnet. Feature-Wünsche haben Vorrang.
- Ein Feature vorschlagen - [Issue eröffnen](https://github.com/0xzerolight/anki_miner/issues).
- Über die Roadmap diskutieren - [Discussions](https://github.com/0xzerolight/anki_miner/discussions).

- **Features**:
  - [x] Auswahl der UI-Sprache.
  - [x] Tab zur lokalen Untertitelerstellung: Opt-in-Tab zum lokalen Erzeugen von Untertiteln.
  - [x] Reading-Tab: Manga und Bücher mining.
  - [x] Backfill-Werkzeug.
  - [ ] Medienbibliothek: Analytics-Tab erweitern, um die lokale Medienbibliothek über alle Medienformen hinweg anzuzeigen.
  - [ ] Automatischer Untertitel-Download.

- **Langfristig**:
  - [x] Android-Portierung -- https://github.com/0xzerolight/anki_miner_android
  - [x] Über Japanisch hinaus: Mining von Chinesisch und Koreanisch.
  - [ ] Anki-Miner-Browsererweiterung.


## Mitwirken

Beiträge jeder Art sind willkommen.
Wenn du das Projekt unterstützen möchtest, teile es bitte mit anderen, denen es nützen könnte.

- Neu hier? Beginne mit [CONTRIBUTING.md](../CONTRIBUTING.md).
- Architekturüberblick: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Verhaltenskodex: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Sicherheit: [SECURITY.md](../SECURITY.md).

Fehlerberichte und Feature-Wünsche -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Allgemeine Fragen und Diskussion -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) oder [Discord](https://discord.com/invite/aDtQyZzUVP).

## Besonderer Dank

Herzlichen Dank an die Personen, die außergewöhnliche Beiträge zum Projekt geleistet haben:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Brillante Feature-Vorschläge, Tests neuer Releases, Community-Aufbau.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Exzellente Feature-Vorschläge, Community-Aufbau und Moderation auf Discord.

In [CONTRIBUTORS.md](../CONTRIBUTORS.md) findest du alle, die auf irgendeine Weise zum Projekt beigetragen haben.


## Lizenz

GNU General Public License v3.0. Siehe [LICENSE](../LICENSE).
