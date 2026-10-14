<!-- i18n-source: README.md sha256:75a9f09207a85972 -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="PyPI-version"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Licens: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="GitHub-downloads"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="GitHub-stjerner"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord-fællesskab"></a>
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
<a href="README.nl.md">Nederlands</a> ·
<a href="README.th.md">ไทย</a> ·
<a href="README.he.md">עברית</a> ·
<a href="README.fa.md">فارسی</a> ·
<a href="README.ca.md">Català</a> ·
<a href="README.nb.md">Norsk bokmål</a> ·
<a href="README.sv.md">Svenska</a> ·
<b>Dansk</b> ·
<a href="README.pl.md">Polski</a> ·
<a href="README.uk.md">Українська</a> ·
<a href="README.hr.md">Hrvatski</a> ·
<a href="README.lt.md">Lietuvių</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Gør autentisk indhold på japansk, kinesisk, koreansk og niogtyve andre sprog til ordforrådskort i Anki.
</p>

<p align="center">
Findes også til Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner til Android</a>.
</p>

<p align="center">
Giv gerne en ⭐ stjerne, hvis Anki Miner har hjulpet dig - det hjælper andre med at finde det :).
</p>


# <p align="center">Demo af udvinding</p>

![Fremvisning af Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Fuld demo med lyd (MP4)</a></p>

### Eksempler på kort

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (lyd)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (lyd)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (lyd)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Installation

### Krav

- **Anki** med tilføjelsesprogrammet [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (kode `2055492159`)
- **ffmpeg** + **libmpv** (kun til videoeksempel) - kun nødvendigt ved installation via pip/pipx eller fra kildekode.

Hent filen til din platform fra den [seneste udgivelse](https://github.com/0xzerolight/anki_miner/releases/latest):

| Platform | Download |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (andre) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Uden AVIF-skærmbilleder og fjernelse af stilhed i genererede undertekster.

### Noter til første start (usignerede versioner)

- **macOS**: åbn `.dmg`-filen, træk **AnkiMiner** over på **Programmer**, og start det. Første start blokeres, fordi appen ikke er notariseret: luk dialogen, åbn appen igen, og vælg **Åbn alligevel** (findes også under **Systemindstillinger** -> **Anonymitet og sikkerhed**). Kun første gang.
- **Linux AppImage**: gør filen eksekverbar, før du kører den - `chmod +x AnkiMiner-*.AppImage`, eller **Egenskaber** -> **Tillad kørsel** i din filhåndtering.
- **Windows SmartScreen**: **Flere oplysninger** -> **Kør alligevel**.
- **Falsk positiv i Windows Defender**: gendan fra **Beskyttelseshistorik**, eller [rapportér det til Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Installér fra PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Japansk, indonesisk og hebraisk kræver ikke noget ekstra. Til et andet udvindingssprog skal du tilføje sprogets motor:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Arabisk og persisk har intet ekstra. Deres data og de europæiske sprogs spaCy-modeller hentes i appen fra Indstillinger -> Udvindingssprog, hvor downloadfilerne ovenfor henter det hele.

</details>

<details>
<summary><strong>Installér fra kildekode</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Den fulde udviklingsopsætning er beskrevet i [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Faner

- **Video** - udvind et enkelt video-/undertekstpar, en batchmappe, YouTube- eller Bilibili-URL'er eller en hel serie til ét kortsæt sorteret efter frekvens.
- **Lydbøger** - udvind lydbøger, podcasts, radio og sange (par af lyd + undertekst/transskription).
- **Læsning** - udvind manga (mokuro), romaner (`.epub`, `.txt`; én bog eller en hel mappe), selvstændige undertekstfiler eller indsat tekst.
- **Analyse** - udvindingshistorik, sværhedsrangeringer, milepæle.
- **Værktøjer** - generér undertekster (lokal Whisper), tidsjustér undertekster (ffsubsync/alass), kondensér medier til lyd med kun dialog, hent video/lyd/undertekster fra ethvert websted, yt-dlp understøtter, kopiér den del af et færdigt kortsæt, der er værd at lære, til et nyt, udfyld felter på eksisterende kort, kør OCR på mangasidebilleder til .mokuro-filer (mokuro), og synkronisér en lydbog med bogens tekst (Lydbogssynk).
- **Indstillinger** - alt, der kan konfigureres.

## Andre funktioner

- Udvindingssprog - japansk, kinesisk, koreansk, engelsk, catalansk, tysk, portugisisk, fransk, spansk, italiensk, nederlandsk, norsk bokmål, rumænsk, græsk, finsk, ungarsk, kroatisk, svensk, polsk, litauisk, dansk, tyrkisk, indonesisk, russisk, arabisk, thai, persisk, slovensk, ukrainsk, vietnamesisk, kantonesisk og hebraisk, som skiftes under Indstillinger. Andre sprog end japansk henter deres motor i appen; indonesisk og hebraisk kræver ingen.
- Ordudvælger - gennemse alle kandidatord, før der oprettes kort, med scenen, mangasiden og ordbogsopslaget side om side.
- Fortryd en kørsel - slet de noter, en kørsel lige har oprettet, direkte fra dens resultatdialog.
- Omfattende filtrering: i+1, interval for frekvensrang, blokeringsliste, regex, ordsæt med mere.
- Import af Yomitan-ordbøger til offlinebrug - definitioner, tonehøjdeaccent, frekvens - kædet efter prioritet.
- Flere frekvenslister kædet efter prioritet.
- Ordlyd på kortene fra lokale lydpakker, JapanesePod101, Google TTS eller Microsoft Edge TTS.
- Sætningslyd på Læsning-kort fra Google Translate TTS eller Naver Papago til japansk og koreansk (slået fra som standard).
- Formatering af ordforklaringer pr. ordbog, som i Yomitan.
- Indbygget videoeksempel med libmpv - afspil et ords scene under udvælgelsen, eller finjustér undertekst-timingen med direkte afspilning.
- Animerede skærmbilleder (se eksemplerne på kort ovenfor).
- Indstillingsprofiler - gem navngivne konfigurationer, og skift mellem dem fra programhovedet.
- Opdater stil på udvundne kort - anvend din nuværende kortstil igen på kort, du allerede har lavet (Værktøjer → Udfyld kort).

<details>
<summary><strong>Indbyggede temaer (29)</strong></summary>

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

Temalicenser: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Vil du have et andet tema tilføjet? Foreslå det i et GitHub-issue.

</details>

<details>
<summary><strong>Sådan virker det</strong></summary>

1. **Læs underteksterne**, og del teksten op i enkelte ord.
2. **Filtrér** ned til indholdsord, du ikke allerede kender - eventuelt med din egen gennemgang af listen i Ordudvælgeren.
3. **Tag et skærmbillede og et lydklip** fra videoen for hver linje.
4. **Slå definitioner op** i dine konfigurerede offlineordbøger.
5. **Send de færdige kort til Anki.**

</details>

## Anbefalede ressourcer

Ordbøger og frekvenslister til alle udvindingssprog med downloadlinks: [RESOURCES.md](../RESOURCES.md). Opsætningsguiden tilbyder det rette sæt til dit udvindingssprog.

<details>
<summary><strong>JMnedict-licens</strong></summary>

Bruger medfølgende navnelister afledt af [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (JMdict/EDICT-projektet, EDRDG, CC BY-SA 4.0).

</details>

## Fejlfinding

| Problem                  | Løsning                                                                          |
|--------------------------|----------------------------------------------------------------------------------|
| "Cannot connect to Anki" | Start Anki, og sørg for, at AnkiConnect er installeret.                          |
| "Deck not found"         | Vælg et eksisterende kortsæt under Indstillinger -> Kort og Anki. Kortsæt oprettes ikke for dig; opret det først i Anki, hvis du har brug for et nyt. |
| "Note type not found"    | Konfigurer feltnavnene for din notetype under Indstillinger -> Kort og Anki.     |
| "ffmpeg not found"       | Installér ffmpeg, og føj det til PATH.                                           |
| Ingen definitioner fundet | Tilføj en Yomitan-ordbog under Indstillinger -> Ordbøger -> Tilføj ordbog… |
| Windows-installationsprogrammet vil ikke åbne / SmartScreen-advarsel | Se [Noter til første start](#noter-til-første-start-usignerede-versioner): vælg **Flere oplysninger** -> **Kør alligevel**; gendan falske positiver i Defender fra **Beskyttelseshistorik**. |
| Ny installation har ingen definitioner | Kør Funktioner -> Opsætningsguide eller Funktioner -> Hent anbefalede ressourcer. Ved manuel import skal Yomitan-ZIP-filen forblive intakt (pak den ikke ud). |
| Tilføj ordbog hænger eller mislykkes | Notér det sidst viste trin, og vedhæft loggene (se "Hvor er loggene?" nedenfor). Angiv ordbogs-ZIP-filens navn, kilde og størrelse i rapporten. |
| Hvor er loggene?         | Brug Hjælp -> Åbn logmappe, eller åbn `%USERPROFILE%\.anki_miner\anki_miner.log` på Windows eller `~/.anki_miner/anki_miner.log` på macOS/Linux. Roterede logge har endelserne `.1` til `.5`. Send også `anki_miner.crash`, hvis den findes (et nedbrud, der lukkede appen, skriver sin stak til den fil, ikke til loggen), og `anki_miner.child.log`, som indeholder output fra en hjælpeproces. |
| Rapportering af en fejl  | Hjælp -> Eksportér diagnosticering… skriver en ZIP-fil til en placering, du vælger, med loggene (`anki_miner.log` og dens rotationer, `anki_miner.crash`, `anki_miner.child.log`), din `settings.json`, dine konfigurations- og UI-tilstandsfiler, øjebliksbilleder af køen og downloadmanifester samt genererede rapporter om maskinens og appens tilstand (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Gennemse den, før du uploader den, for den indeholder filstier og filnavne fra din computer. Intet uploades automatisk. |
| Mere diagnostisk logning | Sæt `ANKI_MINER_LOG_LEVEL=DEBUG`, før du starter Anki Miner, for at få detaljer fra tredjepartsbibliotekerne yt-dlp, urllib3 og fugashi. Standarden er `WARNING`; Anki Miners egne logge forbliver på DEBUG. |
| Lyden er på det forkerte sprog | Værktøjet vælger lydsporet på udvindingssproget, ellers det første. Vælg det selv med Lydspor… (Video -> Enkelt). |
| Underteksterne er ikke synkrone | Brug kontrollen til undertekstforskydning i brugerfladen (interval ±300 sekunder). |

## Bidrag

Bidrag af enhver art er velkomne.
Hvis du vil støtte projektet, så del det gerne med andre, der kan have gavn af det.

- Ny her? Start med [CONTRIBUTING.md](../CONTRIBUTING.md).
- Overblik over arkitekturen: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Adfærdskodeks: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Sikkerhed: [SECURITY.md](../SECURITY.md).

Fejlrapporter og ønsker til funktioner -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Generelle spørgsmål og diskussion -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) eller [Discord](https://discord.com/invite/aDtQyZzUVP).

## Særlig tak

En oprigtig tak til de personer, der har ydet enestående bidrag til projektet:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Fremragende forslag til funktioner, test af nye udgivelser, opbygning af fællesskabet.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Fremragende forslag til funktioner, opbygning af fællesskabet og moderering på Discord.

Se [CONTRIBUTORS.md](../CONTRIBUTORS.md) for alle, der har bidraget til projektet på nogen måde.


## Licens

GNU General Public License v3.0. Se [LICENSE](../LICENSE).
