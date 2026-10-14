<!-- i18n-source: README.md sha256:9e1d902b54a4e2b2 -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="Različica na PyPI"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Licenca: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="Prenosi z GitHuba"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="Zvezdice na GitHubu"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Skupnost na Discordu"></a>
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
<b>Slovenščina</b> ·
<a href="README.ko.md">한국어</a> ·
<a href="README.pt_pt.md">Português (Portugal)</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Izvirne vsebine v japonščini, kitajščini, korejščini in še devetindvajsetih jezikih spremenite v kartice Anki z besediščem.
</p>

<p align="center">
Na voljo tudi za Android – <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner za Android</a>.
</p>

<p align="center">
Če vam je Anki Miner pomagal, mu dodelite ⭐ zvezdico – tako ga drugi lažje najdejo :).
</p>


# <p align="center">Predstavitev zbiranja</p>

![Predstavitev programa Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Celotna predstavitev z zvokom (MP4)</a></p>

### Primeri kartic

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (z zvokom)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (z zvokom)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (z zvokom)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Namestitev

### Zahteve

- **Anki** z dodatkom [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (koda `2055492159`)
- **ffmpeg** + **libmpv** (samo za predogled videa) – potrebna le pri namestitvi s pip/pipx ali iz izvorne kode.

Prenesite datoteko za svojo platformo iz [najnovejše izdaje](https://github.com/0xzerolight/anki_miner/releases/latest):

| Platforma | Prenos |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (drugo) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Brez posnetkov zaslona AVIF in odstranjevanja tišine pri ustvarjenih podnapisih.

### Opombe ob prvem zagonu (nepodpisane različice)

- **macOS**: odprite `.dmg`, povlecite **AnkiMiner** v **Applications** in ga zaženite. Prvi zagon je blokiran, ker aplikacija ni notarizirana: zaprite pogovorno okno, znova odprite aplikacijo in izberite **Open Anyway** (tudi v **System Settings** -> **Privacy & Security**). Samo enkrat.
- **Linux AppImage**: pred zagonom datoteko naredite izvršljivo – `chmod +x AnkiMiner-*.AppImage` ali v upravitelju datotek **Lastnosti** -> **Dovoli izvajanje**.
- **Windows SmartScreen**: **Več informacij** -> **Vseeno zaženi**.
- **Lažno pozitivni zadetek Windows Defender**: obnovite iz **Zgodovina zaščite** ali [prijavite Microsoftu](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Namestitev iz PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Japonščina, indonezijščina in hebrejščina ne potrebujejo ničesar dodatnega. Za drug jezik zbiranja dodajte njegov pogon:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Arabščina in perzijščina nimata dodatka. Njune podatke in modele spaCy za evropske jezike prenesete v aplikaciji v Nastavitve -> Jezik zbiranja, kjer zgoraj navedeni prenosi pridobijo vse potrebno.

</details>

<details>
<summary><strong>Namestitev iz izvorne kode</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Za celotno razvojno okolje glejte [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Zavihki

- **Video** – zbirajte iz posameznega para video/podnapisi, paketne mape, URL-jev YouTube ali Bilibili ali iz cele serije v en komplet, razvrščen po pogostosti.
- **Zvočne knjige** – zbirajte iz zvočnih knjig, podkastov, radia in pesmi (pari zvok + podnapisi/prepis).
- **Branje** – zbirajte iz mange (mokuro), romanov (`.epub`, `.txt`; posamezna knjiga ali cela mapa), samostojnih datotek podnapisov ali prilepljenega besedila.
- **Analitika** – zgodovina zbiranja, razvrstitve po težavnosti, mejniki.
- **Pripomočki** – ustvarjanje podnapisov (lokalni Whisper), časovna uskladitev podnapisov (ffsubsync/alass), zgoščevanje medijev v zvok samo z dialogi, prenos videa/zvoka/podnapisov s katerega koli spletnega mesta, ki ga podpira yt-dlp, kopiranje dela pripravljenega kompleta, ki se ga splača učiti, v nov komplet, dopolnjevanje polj na obstoječih karticah, OCR slik strani mange v datoteke .mokuro (mokuro) in časovna uskladitev zvočne knjige z besedilom knjige (Sinhronizacija).
- **Nastavitve** – vse, kar je mogoče nastaviti.

## Druge funkcije

- Jeziki zbiranja – japonščina, kitajščina, korejščina, angleščina, katalonščina, nemščina, portugalščina, francoščina, španščina, italijanščina, nizozemščina, norveščina (bokmål), romunščina, grščina, finščina, madžarščina, hrvaščina, švedščina, poljščina, litovščina, danščina, turščina, indonezijščina, ruščina, arabščina, tajščina, perzijščina, slovenščina, ukrajinščina, vietnamščina, kantonščina in hebrejščina, ki jih preklapljate v Nastavitvah. Jeziki razen japonščine svoj pogon prenesejo v aplikaciji; indonezijščina in hebrejščina ga ne potrebujeta.
- Kurator besed – preglejte vsako kandidatno besedo, preden se ustvarijo kartice, s prizorom, stranjo mange in slovarskim vnosom drug ob drugem.
- Razveljavitev zagona – izbrišite zapiske, ki jih je zagon pravkar ustvaril, neposredno iz okna z rezultati.
- Obsežno filtriranje: i+1, obseg uvrstitev pogostosti, črni seznam, regularni izrazi, nabori besed in drugo.
- Uvoz slovarjev Yomitan brez povezave – definicije, tonski naglas, pogostost – povezanih v verigo po prednosti.
- Več seznamov pogostosti, povezanih v verigo po prednosti.
- Zvok besed na karticah iz lokalnih zvočnih paketov, JapanesePod101, Google TTS ali Microsoft Edge TTS.
- Zvok stavkov na karticah iz Branja prek Google Translate TTS ali Naver Papago za japonščino in korejščino (privzeto izklopljeno).
- Slog glosarja za vsak slovar posebej, v slogu Yomitan.
- Vdelan predogled videa libmpv – predvajajte prizor besede med izbiranjem ali prilagodite čas podnapisov ob sprotnem predvajanju.
- Animirani posnetki zaslona (glejte primere kartic zgoraj).
- Profili nastavitev – shranite poimenovane konfiguracije in med njimi preklapljajte v glavi okna.
- Preoblikuj zbrane kartice – na že ustvarjenih karticah znova uveljavite trenutni slog kartic (Pripomočki → Dopolni kartice).

<details>
<summary><strong>Vgrajene teme (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (svetla); Frappé, Macchiato, Mocha (temne)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (svetla), Wave (temna)
- **Rosé Pine** - Dawn (svetla); Main, Moon (temni)
- **Solarized** - Light, Dark
- **Samostojne** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Licence tem: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Bi radi dodali še kakšno temo? Predlagajte jo v prijavi (Issue) na GitHubu.

</details>

<details>
<summary><strong>Kako deluje</strong></summary>

1. **Prebere podnapise** in besedilo razdeli na posamezne besede.
2. **Izloči** polnopomenske besede, ki jih še ne poznate – po želji seznam sami pregledate v Kuratorju besed.
3. **Zajame posnetek zaslona in zvočni posnetek** iz videa za vsako vrstico.
4. **Poišče definicije** v nastavljenih slovarjih brez povezave.
5. **Pošlje dokončane kartice v Anki.**

</details>

## Priporočeni viri

Slovarji in seznami pogostosti za vse jezike zbiranja s povezavami za prenos: [RESOURCES.md](../RESOURCES.md). Čarovnik za nastavitev ponudi ustrezen nabor za vaš jezik zbiranja.

<details>
<summary><strong>Licenca JMnedict</strong></summary>

Uporablja priložene nabore imen, izpeljane iz [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (projekt JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Odpravljanje težav

| Težava                    | Rešitev                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| „Povezava z Anki ni mogoča“ | Zaženite Anki in se prepričajte, da je AnkiConnect nameščen.                                  |
| „Komplet ni najden“         | Izberite obstoječi komplet v Nastavitve -> Kartice in Anki. Kompleti se ne ustvarijo samodejno; če potrebujete novega, ga najprej ustvarite v Anki. |
| „Vrsta zapiska ni najdena“    | Nastavite imena polj svoje vrste zapiska v Nastavitve -> Kartice in Anki.               |
| „ffmpeg ni najden“       | Namestite ffmpeg in ga dodajte v PATH.                                               |
| Ni najdenih definicij     | Dodajte slovar Yomitan v Nastavitve -> Slovarji -> Dodaj slovar … |
| Namestitveni program za Windows se ne odpre / opozorilo SmartScreen | Glejte [Opombe ob prvem zagonu](#opombe-ob-prvem-zagonu-nepodpisane-različice): izberite **Več informacij** -> **Vseeno zaženi**; lažno pozitivne zadetke Defenderja obnovite iz **Zgodovina zaščite**. |
| Nova namestitev nima definicij | Zaženite Orodja -> Čarovnik za nastavitev ali Orodja -> Prenesi priporočene vire. Pri ročnem uvozu pustite datoteko ZIP Yomitan nedotaknjeno (ne razširite je). |
| Dodajanje slovarja obstane ali ne uspe | Zapišite si zadnjo vidno fazo in priložite dnevnike (glejte „Kje so dnevniki?“ spodaj). V poročilo vključite ime, vir in velikost datoteke ZIP slovarja. |
| Kje so dnevniki?      | Uporabite Pomoč -> Odpri mapo z dnevniki ali odprite `%USERPROFILE%\.anki_miner\anki_miner.log` v sistemu Windows oziroma `~/.anki_miner/anki_miner.log` v sistemu macOS/Linux. Zamenjani dnevniki imajo pripone od `.1` do `.5`. Pošljite tudi `anki_miner.crash`, če obstaja (zrušitev, ki je zaprla aplikacijo, zapiše sklad v to datoteko, ne v dnevnik), in `anki_miner.child.log`, ki vsebuje izhod pomožnega procesa. |
| Prijava napake          | Pomoč -> Izvozi diagnostiko … zapiše datoteko ZIP na izbrano mesto; vsebuje dnevnike (`anki_miner.log` in njegove zamenjane različice, `anki_miner.crash`, `anki_miner.child.log`), vašo datoteko `settings.json`, datoteke konfiguracije in stanja vmesnika, posnetke čakalnih vrst in manifeste prenosov ter ustvarjena poročila o stanju računalnika in aplikacije (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Pred nalaganjem jo preglejte, ker vsebuje poti in imena datotek z vašega računalnika. Nič se ne naloži samodejno. |
| Podrobnejše diagnostično beleženje | Pred zagonom programa Anki Miner nastavite `ANKI_MINER_LOG_LEVEL=DEBUG`, da zajamete podrobnosti zunanjih knjižnic yt-dlp, urllib3 in fugashi. Privzeto je `WARNING`; dnevniki programa Anki Miner ostanejo na ravni DEBUG. |
| Zvok je v napačnem jeziku  | Orodje izbere zvočno sled v jeziku zbiranja, sicer prvo. Izberite jo sami z Zvočna sled … (Video -> Posamezno). |
| Podnapisi niso usklajeni    | Uporabite nastavitev zamika podnapisov v vmesniku (obseg ±300 sekund).                 |

## Prispevanje

Dobrodošli so prispevki vseh vrst.
Če želite podpreti projekt, ga delite z drugimi, ki bi jim lahko koristil.

- Ste novi? Začnite s [CONTRIBUTING.md](../CONTRIBUTING.md).
- Pregled arhitekture: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Kodeks ravnanja: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Varnost: [SECURITY.md](../SECURITY.md).

Prijave napak in predlogi funkcij -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Splošna vprašanja in razprave -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) ali [Discord](https://discord.com/invite/aDtQyZzUVP).

## Posebna zahvala

Iskrena hvala ljudem, ki so k projektu izjemno prispevali:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - odlični predlogi funkcij, preizkušanje novih izdaj, gradnja skupnosti.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - odlični predlogi funkcij, gradnja skupnosti in moderiranje na Discordu.

Na [CONTRIBUTORS.md](../CONTRIBUTORS.md) so navedeni vsi, ki so kakor koli prispevali k projektu.


## Licenca

GNU General Public License v3.0. Glejte [LICENSE](../LICENSE).
