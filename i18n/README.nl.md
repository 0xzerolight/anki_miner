<!-- i18n-source: README.md sha256:e349c147c5d08d7d -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="PyPI-versie"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Licentie: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="Downloads op GitHub"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="Sterren op GitHub"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord-community"></a>
</p>

<!-- i18n-nav:start -->
<p align="center">
<a href="../README.md">English</a> ·
<a href="README.ja.md">日本語</a> ·
<a href="README.ru.md">Русский</a> ·
<a href="README.fr.md">Français</a> ·
<a href="README.es.md">Español</a> ·
<a href="README.de.md">Deutsch</a> ·
<a href="README.pt_br.md">Português (Brasil)</a> ·
<a href="README.id.md">Bahasa Indonesia</a> ·
<a href="README.vi.md">Tiếng Việt</a> ·
<a href="README.zh_cn.md">简体中文</a> ·
<a href="README.zh_tw.md">繁體中文</a> ·
<a href="README.it.md">Italiano</a> ·
<a href="README.ar.md">العربية</a> ·
<a href="README.yue.md">粵語</a> ·
<a href="README.sl.md">Slovenščina</a> ·
<a href="README.ko.md">한국어</a> ·
<a href="README.pt_pt.md">Português (Portugal)</a> ·
<b>Nederlands</b> ·
<a href="README.th.md">ไทย</a> ·
<a href="README.he.md">עברית</a> ·
<a href="README.fa.md">فارسی</a> ·
<a href="README.ca.md">Català</a> ·
<a href="README.nb.md">Norsk bokmål</a> ·
<a href="README.sv.md">Svenska</a> ·
<a href="README.da.md">Dansk</a> ·
<a href="README.pl.md">Polski</a> ·
<a href="README.uk.md">Українська</a> ·
<a href="README.hr.md">Hrvatski</a> ·
<a href="README.lt.md">Lietuvių</a> ·
<a href="README.fi.md">Suomi</a> ·
<a href="README.hu.md">Magyar</a> ·
<a href="README.el.md">Ελληνικά</a> ·
<a href="README.ro.md">Română</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Maak van authentieke content in het Japans, Chinees, Koreaans en negenentwintig andere talen Anki-woordenschatkaarten.
</p>

<p align="center">
Ook op Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner voor Android</a>.
</p>

<p align="center">
Laat een ⭐ ster achter als Anki Miner je heeft geholpen - zo vinden anderen het ook :).
</p>


# <p align="center">Mining-demo</p>

![Demonstratie van Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Volledige demo met geluid (MP4)</a></p>

### Voorbeeldkaarten

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (geluid)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (geluid)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (geluid)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Installatie

### Vereisten

- **Anki** met de add-on [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (code `2055492159`)
- **ffmpeg** + **libmpv** (alleen voor het videovoorbeeld) - alleen nodig bij installatie via pip/pipx of vanuit de broncode.

Download het bestand voor je platform uit de [nieuwste release](https://github.com/0xzerolight/anki_miner/releases/latest):

| Platform | Download |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (overig) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Zonder AVIF-schermafbeeldingen en stilteverwijdering voor gegenereerde ondertitels.

### Notities bij de eerste start (niet-ondertekende builds)

- **macOS**: open de `.dmg`, sleep **AnkiMiner** naar **Apps** en start het. De eerste start wordt geblokkeerd omdat de app niet genotariseerd is: sluit het venster, open de app opnieuw en kies **Toch openen** (ook te vinden onder **Systeeminstellingen** -> **Privacy en beveiliging**). Dit hoeft maar één keer.
- **Linux AppImage**: maak het bestand uitvoerbaar voordat je het start - `chmod +x AnkiMiner-*.AppImage`, of **Eigenschappen** -> **Uitvoeren toestaan** in je bestandsbeheerder.
- **Windows SmartScreen**: **Meer informatie** -> **Toch uitvoeren**.
- **Vals positief van Windows Defender**: herstel het via **Beveiligingsgeschiedenis** of [meld het bij Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Installeren vanaf PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Japans, Indonesisch en Hebreeuws hebben niets extra nodig. Voeg voor een andere miningtaal de bijbehorende engine toe:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Arabisch en Perzisch hebben geen extra. Hun gegevens, en de spaCy-modellen van de Europese talen, worden in de app gedownload via Instellingen -> Miningtaal; bij de downloads hierboven wordt alles opgehaald.

</details>

<details>
<summary><strong>Installeren vanuit de broncode</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Voor een volledige ontwikkelomgeving, zie [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Tabbladen

- **Video** - minen uit één video met ondertitels, een batchmap, YouTube- of Bilibili-URL's, of een hele serie in één stapel die op frequentie is gerangschikt.
- **Luisterboeken** - minen uit luisterboeken, podcasts, radio en liedjes (audio + ondertitel/transcript).
- **Lezen** - minen uit manga (mokuro), romans (`.epub`, `.txt`; één boek of een hele map), losse ondertitelbestanden of geplakte tekst.
- **Statistieken** - mininggeschiedenis, moeilijkheidsranglijsten, mijlpalen.
- **Hulpmiddelen** - ondertitels genereren (lokale Whisper), ondertitels hertimen (ffsubsync/alass), media condenseren tot audio met alleen dialoog, video/audio/ondertitels downloaden van elke site die yt-dlp ondersteunt, het leerzame deel van een kant-en-klare stapel naar een nieuwe kopiëren, velden van bestaande kaarten aanvullen, mangapagina's met OCR omzetten naar .mokuro-bestanden (mokuro) en een luisterboek synchroniseren met de tekst van het boek (Boeksync).
- **Instellingen** - alles wat je kunt instellen.

## Andere functies

- Miningtalen - Japans, Chinees, Koreaans, Engels, Catalaans, Duits, Portugees, Frans, Spaans, Italiaans, Nederlands, Noors (Bokmål), Roemeens, Grieks, Fins, Hongaars, Kroatisch, Zweeds, Pools, Litouws, Deens, Turks, Indonesisch, Russisch, Arabisch, Thai, Perzisch, Sloveens, Oekraïens, Vietnamees, Kantonees en Hebreeuws, te wisselen in Instellingen. Andere talen dan Japans downloaden hun engine in de app; Indonesisch en Hebreeuws hebben er geen nodig.
- Woordcurator - controleer elk kandidaatwoord voordat er kaarten worden gemaakt, met de scène, mangapagina en woordenboekitem naast elkaar.
- Een run ongedaan maken - verwijder de notities die een run net heeft gemaakt, direct vanuit het resultatenvenster.
- Uitgebreid filteren: i+1, bereik van frequentierang, blacklist, regex, namenlijsten en meer.
- Offline import van Yomitan-woordenboeken - definities, toonhoogteaccent, frequentie - geschakeld op prioriteit.
- Meerdere frequentielijsten, geschakeld op prioriteit.
- Woordaudio op kaarten uit lokale audiopakketten, JapanesePod101, Google TTS of Microsoft Edge TTS.
- Zinsaudio op Lezen-kaarten via Google Translate TTS, of Naver Papago voor Japans en Koreaans (standaard uit).
- Opmaak van definities per woordenboek, in Yomitan-stijl.
- Ingebouwd videovoorbeeld met libmpv - speel de scène van een woord af tijdens het selecteren, of verschuif de ondertiteltiming terwijl de video speelt.
- Geanimeerde schermafbeeldingen (zie de voorbeeldkaarten hierboven).
- Instellingenprofielen - sla benoemde configuraties op en wissel ertussen vanuit de kop van het venster.
- Geminede kaarten opnieuw opmaken - pas je huidige kaartopmaak opnieuw toe op kaarten die je al hebt gemaakt (Hulpmiddelen → Aanvullen).

<details>
<summary><strong>Ingebouwde thema's (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (light); Frappé, Macchiato, Mocha (dark)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (light), Wave (dark)
- **Rosé Pine** - Dawn (light); Main, Moon (dark)
- **Solarized** - Light, Dark
- **Losstaand** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Themalicenties: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Wil je een ander thema erbij? Stel het voor in een GitHub-issue.

</details>

<details>
<summary><strong>Hoe het werkt</strong></summary>

1. **De ondertitels lezen** en de tekst opsplitsen in losse woorden.
2. **Filteren** tot inhoudswoorden die je nog niet kent - eventueel controleer je de lijst zelf in de Woordcurator.
3. **Een schermafbeelding en audioclip** uit de video halen voor elke regel.
4. **Definities opzoeken** in je ingestelde offline woordenboeken.
5. **De kaarten naar Anki sturen.**

</details>

## Aanbevolen bronnen

Woordenboeken en frequentielijsten voor elke miningtaal, met downloadlinks: [RESOURCES.md](../RESOURCES.md). De Configuratiewizard biedt de juiste set aan voor je miningtaal.

<details>
<summary><strong>Licentie van JMnedict</strong></summary>

Gebruikt meegeleverde namenlijsten die zijn afgeleid van [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (JMdict/EDICT-project, EDRDG, CC BY-SA 4.0).

</details>

## Probleemoplossing

| Probleem                 | Oplossing                                                                        |
|--------------------------|----------------------------------------------------------------------------------|
| "Kan geen verbinding maken met Anki" | Start Anki en controleer of AnkiConnect is geïnstalleerd.               |
| "Stapel niet gevonden"   | Kies een bestaande stapel in Instellingen -> Kaarten & Anki. Stapels worden niet voor je aangemaakt; maak er eerst een in Anki als je een nieuwe nodig hebt. |
| "Notitietype niet gevonden" | Stel de veldnamen van je notitietype in via Instellingen -> Kaarten & Anki.   |
| "ffmpeg niet gevonden"   | Installeer ffmpeg en voeg het toe aan PATH.                                      |
| Geen definities gevonden | Voeg een Yomitan-woordenboek toe in Instellingen -> Woordenboeken -> Woordenboek toevoegen… |
| Het Windows-installatieprogramma opent niet / SmartScreen-waarschuwing | Zie [Notities bij de eerste start](#notities-bij-de-eerste-start-niet-ondertekende-builds): kies **Meer informatie** -> **Toch uitvoeren**; herstel valse positieven van Defender via **Beveiligingsgeschiedenis**. |
| Nieuwe installatie heeft geen definities | Voer Extra -> Configuratiewizard of Extra -> Aanbevolen bronnen downloaden uit. Laat bij handmatig importeren de Yomitan-ZIP intact (pak hem niet uit). |
| Woordenboek toevoegen loopt vast of mislukt | Noteer de laatst zichtbare fase en voeg de logboeken toe (zie "Waar staan de logboeken?" hieronder). Vermeld in de melding de naam, de bron en de grootte van de woordenboek-ZIP. |
| Waar staan de logboeken? | Gebruik Help -> Logboekmap openen, of open `%USERPROFILE%\.anki_miner\anki_miner.log` op Windows of `~/.anki_miner/anki_miner.log` op macOS/Linux. Geroteerde logboeken hebben de achtervoegsels `.1` tot en met `.5`. Stuur ook `anki_miner.crash` mee als dat bestaat (een crash die de app liet vastlopen schrijft de stack naar dat bestand, niet naar het logboek), en `anki_miner.child.log`, met de uitvoer van een hulpproces. |
| Een bug melden           | Help -> Diagnostiek exporteren… schrijft een ZIP naar een locatie naar keuze, met de logboeken (`anki_miner.log` en de rotaties ervan, `anki_miner.crash`, `anki_miner.child.log`), je `settings.json`, je configuratie- en UI-statusbestanden, momentopnamen van de wachtrij en downloadmanifesten, en gegenereerde rapporten over de staat van de computer en de app (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Bekijk het bestand voordat je het uploadt, want het bevat bestandspaden en bestandsnamen van je computer. Er wordt niets automatisch geüpload. |
| Meer diagnostische logboekregistratie | Stel `ANKI_MINER_LOG_LEVEL=DEBUG` in voordat je Anki Miner start om details van externe onderdelen (yt-dlp, urllib3 en fugashi) vast te leggen. De standaard is `WARNING`; de logboeken van Anki Miner zelf blijven op DEBUG. |
| Audio in de verkeerde taal | Het programma kiest het audiospoor in de miningtaal, en anders het eerste. Kies het zelf met Audiospoor… (Video -> Enkel). |
| Ondertitels niet synchroon | Gebruik de ondertitelverschuiving in de interface (bereik ±300 seconden).      |

## Bijdragen

Bijdragen van elke soort zijn welkom.
Wil je het project steunen, deel het dan met anderen die er iets aan kunnen hebben.

- Nieuw hier? Begin met [CONTRIBUTING.md](../CONTRIBUTING.md).
- Overzicht van de architectuur: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Gedragscode: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Beveiliging: [SECURITY.md](../SECURITY.md).

Bugmeldingen en functieverzoeken -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Algemene vragen en discussie -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) of [Discord](https://discord.com/invite/aDtQyZzUVP).

## Speciale dank

Oprechte dank aan de mensen die uitzonderlijke bijdragen aan het project hebben geleverd:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Briljante functievoorstellen, testen van nieuwe releases, opbouw van de community.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Uitstekende functievoorstellen, opbouw van de community en moderatie op Discord.

Zie [CONTRIBUTORS.md](../CONTRIBUTORS.md) voor iedereen die op welke manier dan ook aan het project heeft bijgedragen.


## Licentie

GNU General Public License v3.0. Zie [LICENSE](../LICENSE).
