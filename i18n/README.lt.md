<!-- i18n-source: README.md sha256:570dfcaa73bf51ed -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="PyPI versija"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Licencija: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="GitHub atsisiuntimai"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="GitHub žvaigždutės"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord bendruomenė"></a>
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
<a href="README.da.md">Dansk</a> ·
<a href="README.pl.md">Polski</a> ·
<a href="README.uk.md">Українська</a> ·
<a href="README.hr.md">Hrvatski</a> ·
<b>Lietuvių</b> ·
<a href="README.fi.md">Suomi</a> ·
<a href="README.hu.md">Magyar</a> ·
<a href="README.el.md">Ελληνικά</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Paverskite autentišką turinį japonų, kinų, korėjiečių ir dar dvidešimt devyniomis kalbomis Anki žodyno kortelėmis.
</p>

<p align="center">
Taip pat yra Android versija - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner for Android</a>.
</p>

<p align="center">
Jei Anki Miner jums padėjo, palikite ⭐ žvaigždutę - tai padeda kitiems ją rasti :).
</p>


# <p align="center">Rinkimo demonstracija</p>

![Anki Miner demonstracija](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Visa demonstracija su garsu (MP4)</a></p>

### Kortelių pavyzdžiai

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (su garsu)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (su garsu)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (su garsu)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Diegimas

### Reikalavimai

- **Anki** su [AnkiConnect](https://ankiweb.net/shared/info/2055492159) priedu (kodas `2055492159`)
- **ffmpeg** + **libmpv** (tik vaizdo peržiūrai) - reikia tik diegiant per pip/pipx arba iš šaltinio kodo.

Atsisiųskite savo platformai skirtą failą iš [naujausios laidos](https://github.com/0xzerolight/anki_miner/releases/latest):

| Platforma | Atsisiuntimas |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (kiti) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Be AVIF ekrano kopijų ir tylos šalinimo generuojamiems subtitrams.

### Pirmojo paleidimo pastabos (nepasirašytos versijos)

- **macOS**: atverkite `.dmg`, nutempkite **AnkiMiner** į **Applications**, tada paleiskite. Pirmasis paleidimas užblokuojamas, nes programa nenotarizuota: užverkite dialogo langą, vėl atverkite programą ir pasirinkite **Open Anyway** (taip pat skiltyje **System Settings** -> **Privacy & Security**). Tik vieną kartą.
- **Linux AppImage**: prieš paleisdami padarykite failą vykdomąjį - `chmod +x AnkiMiner-*.AppImage` arba failų tvarkytuvėje **Properties** -> **Allow executing**.
- **Windows SmartScreen**: **More info** -> **Run anyway**.
- **Klaidingas Windows Defender aliarmas**: atkurkite failą iš **Protection history** arba [praneškite Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Diegimas iš PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Japonų, indoneziečių ir hebrajų kalboms nieko papildomo nereikia. Kitai rinkimo kalbai pridėkite jos variklį:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Arabų ir persų kalboms papildinio nėra. Jų duomenys ir Europos kalbų spaCy modeliai atsisiunčiami programoje skiltyje Nustatymai -> Rinkimo kalba, kur aukščiau nurodytuose atsisiuntimuose viskas gaunama automatiškai.

</details>

<details>
<summary><strong>Diegimas iš šaltinio kodo</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Visą kūrimo aplinkos sąranką rasite [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Skiltys

- **Video** - rinkite iš vienos vaizdo įrašo ir subtitrų poros, paketinio aplanko, YouTube ar Bilibili URL arba viso serialo į vieną pagal dažnį surikiuotą kaladę.
- **Audioknygos** - rinkite iš audioknygų, tinklalaidžių, radijo, dainų (garso ir subtitrų / transkripcijos poros).
- **Skaitymas** - rinkite iš mangos (mokuro), romanų (`.epub`, `.txt`; viena knyga arba visas aplankas), atskirų subtitrų failų ar įklijuoto teksto.
- **Analitika** - rinkimo istorija, sunkumo reitingai, etapai.
- **Priemonės** - generuokite subtitrus (vietinis Whisper), sutapdinkite subtitrų laiką (ffsubsync/alass), suspauskite mediją iki vien dialogo garso, atsisiųskite vaizdo, garso įrašus ir subtitrus iš bet kurios yt-dlp palaikomos svetainės, nukopijuokite verčiausią mokytis paruoštos kaladės dalį į naują, papildykite esamų kortelių laukus, atpažinkite mangos puslapių vaizdus OCR į .mokuro failus (mokuro) ir sulygiuokite audioknygą su knygos tekstu (Sinchronizuoti).
- **Nustatymai** - viskas, ką galima konfigūruoti.

## Kitos funkcijos

- Rinkimo kalbos - japonų, kinų, korėjiečių, anglų, katalonų, vokiečių, portugalų, prancūzų, ispanų, italų, olandų, norvegų bukmolas, rumunų, graikų, suomių, vengrų, kroatų, švedų, lenkų, lietuvių, danų, turkų, indoneziečių, rusų, arabų, tajų, persų, slovėnų, ukrainiečių, vietnamiečių, kantoniečių ir hebrajų, perjungiamos nustatymuose. Kitų nei japonų kalbų variklis atsisiunčiamas programoje; indoneziečių ir hebrajų kalboms jo nereikia.
- Žodžių kuratorius - peržiūrėkite kiekvieną kandidatinį žodį prieš kuriant korteles, šalia matydami jo sceną, mangos puslapį ir žodyno įrašą.
- Vykdymo anuliavimas - ištrinkite ką tik vykdymo sukurtus užrašus tiesiai iš jo rezultatų lango.
- Platus filtravimas: i+1, dažnio rangų intervalas, juodasis sąrašas, reguliariosios išraiškos, žodžių rinkiniai ir kt.
- Neprisijungus veikiančių Yomitan žodynų importas - apibrėžtys, priegaidės, dažnis - sujungti pagal prioritetą.
- Keli dažnio sąrašai, sujungti pagal prioritetą.
- Žodžių garsas kortelėse iš vietinių garso paketų, JapanesePod101, Google TTS arba Microsoft Edge TTS.
- Sakinių garsas skaitymo kortelėse iš Google Translate TTS arba Naver Papago japonų ir korėjiečių kalboms (pagal numatytuosius išjungta).
- Atskiro kiekvieno žodyno apibrėžčių stilius, kaip Yomitan.
- Įtaisyta libmpv vaizdo peržiūra - atrinkdami grokite žodžio sceną arba koreguokite subtitrų laiką gyvai atkuriant.
- Animuotos ekrano kopijos (žr. kortelių pavyzdžius aukščiau).
- Nustatymų profiliai - įrašykite pavadintas konfigūracijas ir perjunkite jas antraštėje.
- Atnaujinti surinktų kortelių stilių - pritaikykite dabartinį kortelių stilių jau sukurtoms kortelėms (Priemonės → Papildyti).

<details>
<summary><strong>Įtaisytosios temos (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (šviesi); Frappé, Macchiato, Mocha (tamsios)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (šviesi), Wave (tamsi)
- **Rosé Pine** - Dawn (šviesi); Main, Moon (tamsios)
- **Solarized** - Light, Dark
- **Standalone** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Temų licencijos: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Norite, kad būtų pridėta kita tema? Pasiūlykite ją GitHub Issue.

</details>

<details>
<summary><strong>Kaip tai veikia</strong></summary>

1. **Nuskaitomi subtitrai**, o tekstas suskaidomas į atskirus žodžius.
2. **Atrenkami** dar nežinomi turinio žodžiai - pageidaujant sąrašą galite patys peržiūrėti žodžių kuratoriuje.
3. **Kiekvienai eilutei padaroma ekrano kopija ir išskiriamas garso klipas** iš vaizdo įrašo.
4. **Apibrėžčių ieškoma** jūsų nustatytuose neprisijungus veikiančiuose žodynuose.
5. **Paruoštos kortelės siunčiamos į Anki.**

</details>

## Rekomenduojami ištekliai

Kiekvienos rinkimo kalbos žodynai ir dažnio sąrašai su atsisiuntimo nuorodomis: [RESOURCES.md](../RESOURCES.md). Sąrankos vediklis pasiūlo jūsų rinkimo kalbai tinkamą rinkinį.

<details>
<summary><strong>JMnedict licencija</strong></summary>

Naudojami pridedami vardų rinkiniai, sudaryti iš [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (JMdict/EDICT projektas, EDRDG, CC BY-SA 4.0).

</details>

## Trikčių šalinimas

| Problema                    | Sprendimas                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| „Nepavyko prisijungti prie Anki“ | Paleiskite Anki ir įsitikinkite, kad įdiegtas AnkiConnect.                                  |
| „Kaladė nerasta“         | Pasirinkite esamą kaladę skiltyje Nustatymai -> Kortelės ir Anki. Kaladės už jus nekuriamos; jei reikia naujos, pirmiausia sukurkite ją Anki programoje. |
| „Užrašo tipas nerastas“    | Nustatykite užrašo tipo laukų pavadinimus skiltyje Nustatymai -> Kortelės ir Anki.               |
| „ffmpeg nerastas“       | Įdiekite ffmpeg ir įtraukite jį į PATH.                                               |
| Apibrėžčių nerasta     | Pridėkite Yomitan žodyną skiltyje Nustatymai -> Žodynai -> Pridėti žodyną… |
| Windows diegimo programa neatsidaro / SmartScreen įspėjimas | Žr. [pirmojo paleidimo pastabas](#pirmojo-paleidimo-pastabos-nepasirašytos-versijos): pasirinkite **More info** -> **Run anyway**; klaidingus Defender aliarmus atkurkite iš **Protection history**. |
| Naujai įdiegus nėra apibrėžčių | Paleiskite per meniu Įrankiai -> Sąrankos vediklis arba Įrankiai -> Atsisiųsti rekomenduojamus išteklius. Importuodami rankiniu būdu, palikite Yomitan ZIP nepakeistą (neišskleiskite jo). |
| Žodyno pridėjimas stringa arba nepavyksta | Užsirašykite paskutinį matomą etapą ir pridėkite žurnalus (žr. „Kur yra žurnalai?“ žemiau). Pranešime nurodykite žodyno ZIP pavadinimą, šaltinį ir dydį. |
| Kur yra žurnalai?      | Naudokite meniu Žinynas -> Atverti žurnalų aplanką arba atverkite `%USERPROFILE%\.anki_miner\anki_miner.log` Windows sistemoje ar `~/.anki_miner/anki_miner.log` macOS/Linux sistemoje. Rotuoti žurnalai turi priesagas nuo `.1` iki `.5`. Taip pat atsiųskite `anki_miner.crash`, jei jis yra (programą nutraukusi avarija savo dėklą įrašo į tą failą, o ne į žurnalą), ir `anki_miner.child.log`, kuriame yra pagalbinio proceso išvestis. |
| Pranešimas apie klaidą          | Meniu Žinynas -> Eksportuoti diagnostiką… įrašo ZIP į jūsų pasirinktą vietą. Jame yra žurnalai (`anki_miner.log` ir jo rotacijos, `anki_miner.crash`, `anki_miner.child.log`), jūsų `settings.json`, konfigūracijos ir sąsajos būsenos failai, eilių momentinės kopijos ir atsisiuntimų manifestai bei sugeneruotos kompiuterio ir programos būsenos ataskaitos (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Prieš įkeldami jį peržiūrėkite, nes jame yra failų keliai ir failų pavadinimai iš jūsų kompiuterio. Niekas neįkeliama automatiškai. |
| Išsamesnis diagnostinis žurnalas | Prieš paleisdami Anki Miner nustatykite `ANKI_MINER_LOG_LEVEL=DEBUG`, kad būtų užfiksuota trečiųjų šalių yt-dlp, urllib3 ir fugashi informacija. Numatytoji reikšmė – `WARNING`; Anki Miner žurnalai lieka DEBUG lygio. |
| Garsas ne ta kalba  | Programa parenka rinkimo kalbos garso takelį, o jei jo nėra – pirmąjį. Pasirinkite jį patys mygtuku Garso takelis… (Video -> Pavienis). |
| Subtitrai nesinchronizuoti    | Naudokite subtitrų poslinkio valdiklį programos sąsajoje (intervalas ±300 sekundžių).                 |

## Prisidėjimas

Laukiami bet kokie indėliai.
Jei norite paremti projektą, pasidalykite juo su kitais, kuriems jis gali būti naudingas.

- Esate čia naujokas? Pradėkite nuo [CONTRIBUTING.md](../CONTRIBUTING.md).
- Architektūros apžvalga: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Elgesio kodeksas: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Saugumas: [SECURITY.md](../SECURITY.md).

Pranešimai apie klaidas ir funkcijų pasiūlymai -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Bendri klausimai ir diskusijos -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) arba [Discord](https://discord.com/invite/aDtQyZzUVP).

## Ypatingos padėkos

Nuoširdžiai dėkojame žmonėms, ypač prisidėjusiems prie projekto:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - puikūs funkcijų pasiūlymai, naujų laidų testavimas, bendruomenės kūrimas.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - puikūs funkcijų pasiūlymai, bendruomenės kūrimas ir moderavimas Discord.

Visus, kurie kaip nors prisidėjo prie projekto, rasite [CONTRIBUTORS.md](../CONTRIBUTORS.md).


## Licencija

GNU General Public License v3.0. Žr. [LICENSE](../LICENSE).
