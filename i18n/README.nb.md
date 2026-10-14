<!-- i18n-source: README.md sha256:19e0751568de3bbb -->

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
<b>Norsk bokmål</b> ·
<a href="README.sv.md">Svenska</a> ·
<a href="README.da.md">Dansk</a> ·
<a href="README.pl.md">Polski</a> ·
<a href="README.uk.md">Українська</a> ·
<a href="README.hr.md">Hrvatski</a> ·
<a href="README.lt.md">Lietuvių</a> ·
<a href="README.fi.md">Suomi</a> ·
<a href="README.hu.md">Magyar</a> ·
<a href="README.el.md">Ελληνικά</a> ·
<a href="README.ro.md">Română</a> ·
<a href="README.tr.md">Türkçe</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Gjør autentisk innhold på japansk, kinesisk, koreansk og tjueni andre språk om til ordforrådskort i Anki.
</p>

<p align="center">
Finnes også for Android – <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner for Android</a>.
</p>

<p align="center">
Gi gjerne en ⭐ stjerne hvis Anki Miner har hjulpet deg – det hjelper andre å finne det :).
</p>


# <p align="center">Utvinningsdemo</p>

![Demonstrasjon av Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Full demo med lyd (MP4)</a></p>

### Eksempelkort

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (lyd)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (lyd)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (lyd)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Installasjon

### Krav

- **Anki** med tillegget [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (kode `2055492159`)
- **ffmpeg** + **libmpv** (bare for videoforhåndsvisning) – trengs bare ved installasjon via pip/pipx eller fra kildekoden.

Hent nedlastingen for plattformen din fra [den nyeste versjonen](https://github.com/0xzerolight/anki_miner/releases/latest):

| Plattform | Nedlasting |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (annet) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Uten AVIF-skjermbilder og fjerning av stillhet for genererte undertekster.

### Merknader ved første oppstart (usignerte versjoner)

- **macOS**: åpne `.dmg`-filen, dra **AnkiMiner** til **Programmer**, og start det. Den første oppstarten blokkeres fordi appen ikke er notarisert: lukk dialogboksen, åpne appen igjen, og velg **Åpne likevel** (også under **Systeminnstillinger** -> **Personvern og sikkerhet**). Bare én gang.
- **Linux AppImage**: gjør filen kjørbar før du kjører den – `chmod +x AnkiMiner-*.AppImage`, eller **Egenskaper** -> **Tillat kjøring** i filbehandleren.
- **Windows SmartScreen**: **Mer informasjon** -> **Kjør likevel**.
- **Falsk positiv i Windows Defender**: gjenopprett fra **Beskyttelseslogg** eller [rapporter til Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Installer fra PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Japansk, indonesisk og hebraisk trenger ikke noe ekstra. For et annet utvinningsspråk legger du til motoren for språket:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Arabisk og persisk har ikke noe ekstra. Dataene deres, og spaCy-modellene for de europeiske språkene, lastes ned i appen fra Innstillinger -> Utvinningsspråk, der nedlastingene over henter alt.

</details>

<details>
<summary><strong>Installer fra kildekoden</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

For et fullstendig utviklingsoppsett, se [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Faner

- **Video** – utvinn et enkelt video- og undertekstpar, en mappe satsvis, YouTube- eller Bilibili-URL-er, eller en hel serie til én frekvensrangert kortstokk.
- **Lydbøker** – utvinn lydbøker, podkaster, radio og sanger (par av lyd og undertekst/transkripsjon).
- **Lesing** – utvinn manga (mokuro), romaner (`.epub`, `.txt`; én bok eller en hel mappe), frittstående undertekstfiler eller innlimt tekst.
- **Analyse** – utvinningshistorikk, vanskelighetsrangeringer, milepæler.
- **Hjelpemidler** – generer undertekster (lokal Whisper), tidsjuster undertekster (ffsubsync/alass), kondenser medier til lyd med bare dialog, last ned video/lyd/undertekster fra alle nettsteder yt-dlp støtter, kopier den delen av en ferdig kortstokk som er verdt å lære, til en ny, etterfyll felt på eksisterende kort, kjør OCR på mangasider til .mokuro-filer (mokuro), og synkroniser en lydbok med teksten i boken (Lydboksynk).
- **Innstillinger** – alt som kan konfigureres.

## Andre funksjoner

- Utvinningsspråk – japansk, kinesisk, koreansk, engelsk, katalansk, tysk, portugisisk, fransk, spansk, italiensk, nederlandsk, norsk bokmål, rumensk, gresk, finsk, ungarsk, kroatisk, svensk, polsk, litauisk, dansk, tyrkisk, indonesisk, russisk, arabisk, thai, persisk, slovensk, ukrainsk, vietnamesisk, kantonesisk og hebraisk, som du bytter mellom i Innstillinger. Andre språk enn japansk laster ned motoren sin i appen; indonesisk og hebraisk trenger ingen.
- Ordkurator – gå gjennom alle kandidatordene før kortene lages, med scenen, mangasiden og ordbokoppføringen side om side.
- Angre en kjøring – slett notatene en kjøring nettopp opprettet, rett fra resultatdialogen.
- Omfattende filtrering: i+1, område for frekvensrang, svarteliste, regex, ordsett og mer.
- Frakoblet import av Yomitan-ordbøker – definisjoner, tonehøydeaksent, frekvens – kjedet etter prioritet.
- Flere frekvenslister kjedet etter prioritet.
- Ordlyd på kortene fra lokale lydpakker, JapanesePod101, Google TTS eller Microsoft Edge TTS.
- Setningslyd på lesekort fra Google Translate TTS, eller Naver Papago for japansk og koreansk (av som standard).
- Stil for ordforklaringer per ordbok, som i Yomitan.
- Innebygd videoforhåndsvisning med libmpv – spill av scenen til et ord mens du kuraterer, eller finjuster undertekst-timingen med direkte avspilling.
- Animerte skjermbilder (se eksempelkortene over).
- Innstillingsprofiler – lagre navngitte konfigurasjoner og bytt mellom dem fra toppteksten.
- Restil utvunnede kort – bruk den gjeldende kortstilen på nytt på kort du allerede har laget (Hjelpemidler → Etterfyll kort).

<details>
<summary><strong>Innebygde temaer (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (lys); Frappé, Macchiato, Mocha (mørk)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (lys), Wave (mørk)
- **Rosé Pine** - Dawn (lys); Main, Moon (mørk)
- **Solarized** - Light, Dark
- **Frittstående** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Temalisenser: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Vil du ha et nytt tema? Foreslå det i en GitHub-sak.

</details>

<details>
<summary><strong>Slik fungerer det</strong></summary>

1. **Les undertekstene** og del teksten opp i enkeltord.
2. **Filtrer** ned til innholdsord du ikke allerede kan – eventuelt med egen gjennomgang av listen i ordkuratoren.
3. **Ta et skjermbilde og et lydklipp** fra videoen for hver linje.
4. **Slå opp definisjoner** i de frakoblede ordbøkene du har konfigurert.
5. **Send de ferdige kortene til Anki.**

</details>

## Anbefalte ressurser

Ordbøker og frekvenslister for alle utvinningsspråk, med nedlastingslenker: [RESOURCES.md](../RESOURCES.md). Oppsettsveiviseren tilbyr riktig sett for utvinningsspråket ditt.

<details>
<summary><strong>Lisens for JMnedict</strong></summary>

Bruker medfølgende navnesett avledet fra [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (JMdict/EDICT-prosjektet, EDRDG, CC BY-SA 4.0).

</details>

## Feilsøking

| Problem                  | Løsning                                                                          |
|--------------------------|----------------------------------------------------------------------------------|
| «Kan ikke koble til Anki» | Start Anki, og kontroller at AnkiConnect er installert.                         |
| «Fant ikke kortstokken»  | Velg en eksisterende kortstokk i Innstillinger -> Kort og Anki. Kortstokker opprettes ikke for deg; lag den i Anki først hvis du trenger en ny. |
| «Fant ikke notattypen»   | Konfigurer feltnavnene for notattypen i Innstillinger -> Kort og Anki.           |
| «Fant ikke ffmpeg»       | Installer ffmpeg, og legg det til i PATH.                                        |
| Ingen definisjoner funnet | Legg til en Yomitan-ordbok i Innstillinger -> Ordbøker -> Legg til ordbok… |
| Installasjonsprogrammet for Windows åpnes ikke / SmartScreen-advarsel | Se [Merknader ved første oppstart](#merknader-ved-første-oppstart-usignerte-versjoner): velg **Mer informasjon** -> **Kjør likevel**; gjenopprett falske positiver i Defender fra **Beskyttelseslogg**. |
| Ny installasjon har ingen definisjoner | Kjør Verktøy -> Oppsettsveiviser eller Verktøy -> Last ned anbefalte ressurser. Ved manuell import beholder du Yomitan-ZIP-filen intakt (ikke pakk den ut). |
| Legg til ordbok henger eller mislykkes | Noter det siste synlige trinnet, og legg ved loggene (se «Hvor er loggene?» nedenfor). Ta med navnet på ordbokens ZIP-fil, kilden og størrelsen i rapporten. |
| Hvor er loggene?         | Bruk Hjelp -> Åpne loggmappen, eller åpne `%USERPROFILE%\.anki_miner\anki_miner.log` på Windows eller `~/.anki_miner/anki_miner.log` på macOS/Linux. Roterte logger har endelsene `.1` til `.5`. Send også `anki_miner.crash` hvis den finnes (et krasj som tok ned appen, skriver stakken sin til den filen, ikke til loggen), og `anki_miner.child.log`, som inneholder utdataene fra en hjelpeprosess. |
| Rapportere en feil       | Hjelp -> Eksporter diagnostikk… skriver en ZIP-fil til et sted du velger, med loggene (`anki_miner.log` og rotasjonene, `anki_miner.crash`, `anki_miner.child.log`), `settings.json`, konfigurasjons- og grensesnittilstandsfilene dine, øyeblikksbilder av køen og nedlastingsmanifester, samt genererte rapporter om maskin- og apptilstanden (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Se gjennom den før du laster den opp, fordi den inneholder filbaner og filnavn fra datamaskinen din. Ingenting lastes opp automatisk. |
| Mer diagnostisk logging | Sett `ANKI_MINER_LOG_LEVEL=DEBUG` før du starter Anki Miner for å fange opp detaljer fra tredjepartsbibliotekene yt-dlp, urllib3 og fugashi. Standard er `WARNING`; loggene fra Anki Miner forblir på DEBUG. |
| Lyden er på feil språk   | Verktøyet velger lydsporet for utvinningsspråket, ellers det første. Velg det selv med Lydspor… (Video -> Enkeltvis). |
| Undertekstene er ute av synk | Bruk kontrollen for undertekstforskyvning i grensesnittet (område ±300 sekunder). |

## Bidra

Alle slags bidrag er velkomne.
Hvis du vil støtte prosjektet, kan du dele det med andre som kan ha nytte av det.

- Ny her? Begynn med [CONTRIBUTING.md](../CONTRIBUTING.md).
- Oversikt over arkitekturen: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Retningslinjer for oppførsel: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Sikkerhet: [SECURITY.md](../SECURITY.md).

Feilrapporter og funksjonsønsker -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Generelle spørsmål og diskusjon -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) eller [Discord](https://discord.com/invite/aDtQyZzUVP).

## Spesiell takk

En oppriktig takk til dem som har gitt usedvanlige bidrag til prosjektet:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** – Glimrende funksjonsforslag, testing av nye versjoner, bygging av fellesskapet.
- ★ **[rob-olvr](https://github.com/rob-olvr)** – Utmerkede funksjonsforslag, bygging av fellesskapet og moderering på Discord.

Se [CONTRIBUTORS.md](../CONTRIBUTORS.md) for alle som har bidratt til prosjektet på noen måte.


## Lisens

GNU General Public License v3.0. Se [LICENSE](../LICENSE).
