<!-- i18n-source: README.md sha256:e349c147c5d08d7d -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="PyPI-version"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Licens: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="Nedladdningar på GitHub"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="Stjärnor på GitHub"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord-gemenskap"></a>
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
<b>Svenska</b> ·
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
Gör om autentiskt innehåll på japanska, kinesiska, koreanska och tjugonio andra språk till ordförrådskort i Anki.
</p>

<p align="center">
Finns även för Android – <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner för Android</a>.
</p>

<p align="center">
Lämna gärna en ⭐ stjärna om Anki Miner har hjälpt dig – det hjälper andra att hitta det :).
</p>


# <p align="center">Insamlingsdemo</p>

![Anki Miner i användning](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Hela demon med ljud (MP4)</a></p>

### Exempelkort

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (ljud)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (ljud)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (ljud)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Installation

### Krav

- **Anki** med tillägget [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (kod `2055492159`)
- **ffmpeg** + **libmpv** (endast för videoförhandsvisning) – behövs bara vid installation via pip/pipx eller från källkod.

Hämta nedladdningen för din plattform från den [senaste versionen](https://github.com/0xzerolight/anki_miner/releases/latest):

| Plattform | Nedladdning |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (övriga) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Saknar AVIF-skärmbilder och tystnadsborttagning för skapade undertexter.

### Vid första start (osignerade versioner)

- **macOS**: öppna `.dmg`-filen, dra **AnkiMiner** till **Program** och starta den. Första starten blockeras eftersom appen inte är notariserad: stäng dialogrutan, öppna appen igen och välj **Öppna ändå** (finns även under **Systeminställningar** -> **Integritet och säkerhet**). Bara en gång.
- **Linux AppImage**: gör filen körbar innan du startar den – `chmod +x AnkiMiner-*.AppImage`, eller **Egenskaper** -> **Tillåt körning** i filhanteraren.
- **Windows SmartScreen**: **Mer information** -> **Kör ändå**.
- **Falsklarm från Windows Defender**: återställ filen från **Skyddshistorik** eller [rapportera till Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Installera från PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Japanska, indonesiska och hebreiska behöver inget extra. För andra insamlingsspråk lägger du till språkets motor:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Arabiska och persiska har inget tillägg. Deras data, och spaCy-modellerna för de europeiska språken, laddas ned i appen från Inställningar -> Insamlingsspråk, där nedladdningarna ovan hämtar allt.

</details>

<details>
<summary><strong>Installera från källkod</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

För en fullständig utvecklingsmiljö, se [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Flikar

- **Video** – samla in ett enskilt par av video och undertext, en batchmapp, URL:er från YouTube eller Bilibili, eller en hel serie till en frekvensrangordnad kortlek.
- **Ljudböcker** – samla in ljudböcker, poddar, radio och låtar (par av ljud och undertext/transkription).
- **Läsning** – samla in manga (mokuro), romaner (`.epub`, `.txt`; en enskild bok eller en hel mapp), fristående undertextfiler eller inklistrad text.
- **Analys** – insamlingshistorik, svårighetsrankning, milstolpar.
- **Hjälpprogram** – skapa undertexter (lokal Whisper), tidsjustera undertexter (ffsubsync/alass), kondensera media till ljud med bara dialog, ladda ned video/ljud/undertexter från alla webbplatser som yt-dlp stöder, kopiera den del av en färdig kortlek som är värd att lära sig till en ny, fyll i fält på befintliga kort, kör OCR på mangasidor till .mokuro-filer (mokuro) och synka en ljudbok med bokens text (Ljudbokssynk).
- **Inställningar** – allt som går att konfigurera.

## Andra funktioner

- Insamlingsspråk – japanska, kinesiska, koreanska, engelska, katalanska, tyska, portugisiska, franska, spanska, italienska, nederländska, norskt bokmål, rumänska, grekiska, finska, ungerska, kroatiska, svenska, polska, litauiska, danska, turkiska, indonesiska, ryska, arabiska, thailändska, persiska, slovenska, ukrainska, vietnamesiska, kantonesiska och hebreiska, som du byter mellan i Inställningar. Andra språk än japanska laddar ned sin motor i appen; indonesiska och hebreiska behöver ingen.
- Ordgranskaren – granska varje kandidatord innan korten skapas, med scenen, mangasidan och ordboksposten sida vid sida.
- Ångra en körning – ta bort anteckningarna som en körning just skapade, direkt från resultatdialogen.
- Omfattande filtrering: i+1, intervall för frekvensrang, svartlista, regex, ordlistor och mer.
- Import av Yomitan-ordböcker för offlinebruk – definitioner, tonhöjdsaccent, frekvens – i en kedja efter prioritet.
- Flera frekvenslistor i en kedja efter prioritet.
- Ordljud på korten från lokala ljudpaket, JapanesePod101, Google TTS eller Microsoft Edge TTS.
- Meningsljud på läsningskort från Google Translate TTS, eller Naver Papago för japanska och koreanska (avstängt som standard).
- Formatering av ordförklaringar per ordbok, i Yomitan-stil.
- Inbyggd videoförhandsvisning med libmpv – spela upp ett ords scen medan du granskar, eller finjustera undertexternas tidsinställning med direktuppspelning.
- Animerade skärmbilder (se exempelkorten ovan).
- Inställningsprofiler – spara namngivna konfigurationer och växla mellan dem från programhuvudet.
- Formatera om insamlade kort – tillämpa din nuvarande kortformatering på kort som du redan har skapat (Hjälpprogram → Fyll i kort).

<details>
<summary><strong>Inbyggda teman (29)</strong></summary>

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
Vill du att ett annat tema läggs till? Föreslå det i ett GitHub-ärende.

</details>

<details>
<summary><strong>Så fungerar det</strong></summary>

1. **Läser undertexterna** och delar upp texten i enskilda ord.
2. **Filtrerar** fram innehållsord som du inte redan kan – och låter dig om du vill granska listan själv i Ordgranskaren.
3. **Tar en skärmbild och ett ljudklipp** från videon för varje rad.
4. **Slår upp definitioner** i dina konfigurerade offlineordböcker.
5. **Skickar de färdiga korten till Anki.**

</details>

## Rekommenderade resurser

Ordböcker och frekvenslistor för alla insamlingsspråk, med nedladdningslänkar: [RESOURCES.md](../RESOURCES.md). Installationsguiden erbjuder rätt uppsättning för ditt insamlingsspråk.

<details>
<summary><strong>Licens för JMnedict</strong></summary>

Använder medföljande namnordlistor som bygger på [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (JMdict/EDICT-projektet, EDRDG, CC BY-SA 4.0).

</details>

## Felsökning

| Problem                  | Lösning                                                                          |
|--------------------------|----------------------------------------------------------------------------------|
| ”Cannot connect to Anki” | Starta Anki och kontrollera att AnkiConnect är installerat.                      |
| ”Deck not found”         | Välj en befintlig kortlek i Inställningar -> Kort och Anki. Kortlekar skapas inte åt dig; skapa den i Anki först om du behöver en ny. |
| ”Note type not found”    | Ställ in fältnamnen för din anteckningstyp i Inställningar -> Kort och Anki.     |
| ”ffmpeg not found”       | Installera ffmpeg och lägg till det i PATH.                                      |
| Inga definitioner hittas | Lägg till en Yomitan-ordbok i Inställningar -> Ordböcker -> Lägg till ordbok… |
| Windows-installationsprogrammet öppnas inte / SmartScreen-varning | Se [Vid första start](#vid-första-start-osignerade-versioner): välj **Mer information** -> **Kör ändå**; återställ falsklarm från Defender via **Skyddshistorik**. |
| En ny installation har inga definitioner | Kör Verktyg -> Installationsguide eller Verktyg -> Ladda ned rekommenderade resurser. Vid manuell import ska Yomitan-ZIP-filen vara intakt (packa inte upp den). |
| Lägg till ordbok fastnar eller misslyckas | Notera det sista synliga steget och bifoga loggar (se ”Var finns loggarna?” nedan). Ange ordbokens ZIP-namn, källa och storlek i rapporten. |
| Var finns loggarna?      | Använd Hjälp -> Öppna loggmappen, eller öppna `%USERPROFILE%\.anki_miner\anki_miner.log` på Windows eller `~/.anki_miner/anki_miner.log` på macOS/Linux. Roterade loggar har suffixen `.1` till `.5`. Skicka även `anki_miner.crash` om den finns (en krasch som stängde appen skriver sin stack till den filen, inte till loggen), och `anki_miner.child.log`, som innehåller utdata från en hjälpprocess. |
| Rapportera ett fel       | Hjälp -> Exportera diagnostik… skriver en ZIP-fil till en plats som du väljer, med loggarna (`anki_miner.log` och dess rotationer, `anki_miner.crash`, `anki_miner.child.log`), din `settings.json`, dina konfigurations- och gränssnittstillståndsfiler, ögonblicksbilder av köer och nedladdningsmanifest, samt genererade rapporter om datorns och appens tillstånd (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Granska den innan du laddar upp den, eftersom den innehåller sökvägar och filnamn från din dator. Inget laddas upp automatiskt. |
| Mer diagnostisk loggning | Ange `ANKI_MINER_LOG_LEVEL=DEBUG` innan du startar Anki Miner för att fånga detaljer från tredjepartsbiblioteken yt-dlp, urllib3 och fugashi. Standard är `WARNING`; Anki Miners egna loggar ligger kvar på DEBUG. |
| Ljudet är på fel språk   | Verktyget väljer ljudspåret på insamlingsspråket, annars det första. Välj det själv med Ljudspår… (Video -> Enskild). |
| Undertexterna är osynkade | Använd reglaget för undertextförskjutning i gränssnittet (intervall ±300 sekunder). |

## Bidra

Bidrag av alla slag är välkomna.
Om du vill stödja projektet får du gärna dela det med andra som kan ha nytta av det.

- Ny här? Börja med [CONTRIBUTING.md](../CONTRIBUTING.md).
- Översikt över arkitekturen: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Uppförandekod: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Säkerhet: [SECURITY.md](../SECURITY.md).

Felrapporter och funktionsförslag -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Allmänna frågor och diskussion -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) eller [Discord](https://discord.com/invite/aDtQyZzUVP).

## Särskilt tack

Ett varmt tack till dem som har gjort exceptionella bidrag till projektet:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** – Lysande funktionsförslag, testning av nya versioner, communitybygge.
- ★ **[rob-olvr](https://github.com/rob-olvr)** – Utmärkta funktionsförslag, communitybygge och moderering på Discord.

Se [CONTRIBUTORS.md](../CONTRIBUTORS.md) för alla som har bidragit till projektet på något sätt.


## Licens

GNU General Public License v3.0. Se [LICENSE](../LICENSE).
