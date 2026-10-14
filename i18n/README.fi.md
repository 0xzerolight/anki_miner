<!-- i18n-source: README.md sha256:570dfcaa73bf51ed -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="PyPI-versio"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Lisenssi: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="GitHub-lataukset"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="GitHub-tähdet"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord-yhteisö"></a>
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
<a href="README.lt.md">Lietuvių</a> ·
<b>Suomi</b> ·
<a href="README.hu.md">Magyar</a> ·
<a href="README.el.md">Ελληνικά</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Tee japanin-, kiinan- ja koreankielisestä sekä 29 muun kielen aidosta sisällöstä Anki-sanastokortteja.
</p>

<p align="center">
Myös Androidilla - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner Androidille</a>.
</p>

<p align="center">
Jätä ⭐ tähti, jos Anki Minerista oli sinulle apua - se auttaa muita löytämään sen :).
</p>


# <p align="center">Louhintademo</p>

![Anki Minerin esittely](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Koko demo äänen kanssa (MP4)</a></p>

### Esimerkkikortit

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (ääni)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (ääni)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (ääni)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Asennus

### Vaatimukset

- **Anki** ja [AnkiConnect](https://ankiweb.net/shared/info/2055492159)-lisäosa (koodi `2055492159`)
- **ffmpeg** + **libmpv** (vain videon esikatseluun) - tarvitaan vain, kun asennat pip/pipx-työkalulla tai lähdekoodista.

Lataa alustasi tiedosto [uusimmasta julkaisusta](https://github.com/0xzerolight/anki_miner/releases/latest):

| Alusta | Lataus |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (muu) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Ei sisällä AVIF-kuvakaappauksia eikä luotujen tekstitysten hiljaisuuden poistoa.

### Ensimmäinen käynnistys (allekirjoittamattomat versiot)

- **macOS**: avaa `.dmg`, vedä **AnkiMiner** **Ohjelmat**-kansioon ja käynnistä se. Ensimmäinen käynnistys estetään, koska sovellusta ei ole notarisoitu: sulje ikkuna, avaa sovellus uudelleen ja valitse **Avaa silti** (myös kohdassa **Järjestelmäasetukset** -> **Tietosuoja ja turvallisuus**). Vain kerran.
- **Linux AppImage**: tee tiedostosta suoritettava ennen käynnistystä - `chmod +x AnkiMiner-*.AppImage` tai tiedostonhallinnassa **Ominaisuudet** -> **Salli suorittaminen**.
- **Windows SmartScreen**: **Lisätietoja** -> **Suorita silti**.
- **Windows Defenderin väärä hälytys**: palauta tiedosto kohdasta **Suojaushistoria** tai [ilmoita Microsoftille](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Asennus PyPI:stä (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Japani, indonesia ja heprea eivät tarvitse mitään lisää. Muulle louhintakielelle lisää sen kielimoottori:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Arabialle ja persialle ei ole lisäosaa. Niiden tiedot sekä eurooppalaisten kielten spaCy-mallit ladataan sovelluksessa kohdasta Asetukset -> Louhintakieli, jossa yllä mainitut lataukset hakevat kaiken.

</details>

<details>
<summary><strong>Asennus lähdekoodista</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Täydelliset kehitysympäristön ohjeet: [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Välilehdet

- **Video** - louhi yksittäinen video- ja tekstityspari, eräkansio, YouTube- tai Bilibili-URL-osoitteita tai koko sarja yhteen yleisyyden mukaan järjestettyyn pakkaan.
- **Äänikirjat** - louhi äänikirjoja, podcasteja, radiota ja kappaleita (ääni + tekstitys- tai litterointiparit).
- **Lukeminen** - louhi mangaa (mokuro), romaaneja (`.epub`, `.txt`; yksittäinen kirja tai koko kansio), erillisiä tekstitystiedostoja tai liitettyä tekstiä.
- **Analytiikka** - louhintahistoria, vaikeusjärjestykset, virstanpylväät.
- **Apuvälineet** - luo tekstityksiä (paikallinen Whisper), ajoita tekstityksiä uudelleen (ffsubsync/alass), tiivistä media pelkäksi dialogiääneksi, lataa videota, ääntä tai tekstityksiä mistä tahansa yt-dlp:n tukemasta palvelusta, kopioi valmiin pakan oppimisen arvoinen osa uuteen pakkaan, täydennä olemassa olevien korttien kenttiä, tunnista mangasivujen kuvista teksti OCR:llä .mokuro-tiedostoiksi (mokuro) ja ajoita äänikirja kirjansa tekstiin (Synkronointi).
- **Asetukset** - kaikki säädettävä.

## Muut ominaisuudet

- Louhintakielet - japani, kiina, korea, englanti, katalaani, saksa, portugali, ranska, espanja, italia, hollanti, norjan bokmål, romania, kreikka, suomi, unkari, kroatia, ruotsi, puola, liettua, tanska, turkki, indonesia, venäjä, arabia, thai, persia, sloveeni, ukraina, vietnam, kantoninkiina ja heprea; kieli vaihdetaan asetuksissa. Muut kielet kuin japani lataavat kielimoottorinsa sovelluksessa; indonesia ja heprea eivät tarvitse sellaista.
- Sanakuraattori - tarkista jokainen ehdokassana ennen korttien luontia, ja näe sen kohtaus, mangasivu ja sanakirjamerkintä rinnakkain.
- Ajon kumoaminen - poista ajon juuri luomat muistiinpanot suoraan sen tulosikkunasta.
- Monipuolinen suodatus: i+1, yleisyyssijan väli, estolista, regex, sanastot ja paljon muuta.
- Offline-Yomitan-sanakirjojen tuonti - määritelmät, sävelkorko, yleisyys - ketjutettuna tärkeysjärjestykseen.
- Useita yleisyyslistoja ketjutettuna tärkeysjärjestykseen.
- Sanojen ääni kortteihin paikallisista äänipaketeista, JapanesePod101:stä, Google TTS:stä tai Microsoft Edge TTS:stä.
- Lauseen ääni Lukeminen-kortteihin Google Translate TTS:stä tai japanille ja korealle Naver Papagosta (oletuksena pois päältä).
- Sanakirjakohtainen selitysten muotoilu Yomitanin tapaan.
- Upotettu libmpv-videoesikatselu - toista sanan kohtaus kuratoinnin aikana tai hienosäädä tekstitysten ajoitusta toiston aikana.
- Animoidut kuvakaappaukset (katso esimerkkikortit yllä).
- Asetusprofiilit - tallenna nimettyjä kokoonpanoja ja vaihda niiden välillä yläpalkista.
- Muotoile louhitut kortit uudelleen - käytä nykyistä korttityyliäsi jo tekemiisi kortteihin (Apuvälineet → Täydennys).

<details>
<summary><strong>Sisäänrakennetut teemat (29)</strong></summary>

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

Teemojen lisenssit: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Haluatko lisää teemoja? Ehdota GitHub Issuessa.

</details>

<details>
<summary><strong>Miten se toimii</strong></summary>

1. **Lukee tekstitykset** ja jakaa tekstin yksittäisiksi sanoiksi.
2. **Suodattaa** jäljelle sisältösanat, joita et vielä tunne - halutessasi voit tarkistaa listan itse Sanakuraattorissa.
3. **Ottaa kuvakaappauksen ja äänileikkeen** videosta jokaiselle riville.
4. **Hakee määritelmät** määrittämistäsi offline-sanakirjoista.
5. **Lähettää valmiit kortit Ankiin.**

</details>

## Suositellut resurssit

Sanakirjat ja yleisyyslistat jokaiselle louhintakielelle latauslinkkeineen: [RESOURCES.md](../RESOURCES.md). Ohjattu käyttöönotto tarjoaa louhintakielellesi sopivan kokoelman.

<details>
<summary><strong>JMnedictin lisenssi</strong></summary>

Käyttää mukana tulevia nimisanastoja, jotka perustuvat [JMnedictiin](https://www.edrdg.org/enamdict/enamdict_doc.html) (JMdict/EDICT-projekti, EDRDG, CC BY-SA 4.0).

</details>

## Vianmääritys

| Ongelma                  | Ratkaisu                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| ”Ankiin ei saada yhteyttä” | Käynnistä Anki ja varmista, että AnkiConnect on asennettu.                     |
| ”Pakkaa ei löydy”        | Valitse olemassa oleva pakka kohdassa Asetukset -> Kortit ja Anki. Pakkoja ei luoda puolestasi; jos tarvitset uuden, luo se ensin Ankissa. |
| ”Muistiinpanotyyppiä ei löydy” | Määritä muistiinpanotyyppisi kenttien nimet kohdassa Asetukset -> Kortit ja Anki. |
| ”ffmpegiä ei löydy”      | Asenna ffmpeg ja lisää se PATHiin.                                               |
| Määritelmiä ei löydy     | Lisää Yomitan-sanakirja kohdassa Asetukset -> Sanakirjat -> Lisää sanakirja… |
| Windows-asennusohjelma ei avaudu / SmartScreen-varoitus | Katso [Ensimmäinen käynnistys](#ensimmäinen-käynnistys-allekirjoittamattomat-versiot): valitse **Lisätietoja** -> **Suorita silti**; palauta Defenderin väärät hälytykset kohdasta **Suojaushistoria**. |
| Uudessa asennuksessa ei ole määritelmiä | Suorita Työkalut -> Ohjattu käyttöönotto tai Työkalut -> Lataa suositellut resurssit. Käsin tuodessa pidä Yomitanin ZIP-tiedosto ehjänä (älä pura sitä). |
| Sanakirjan lisäys jumittuu tai epäonnistuu | Merkitse muistiin viimeinen näkyvä vaihe ja liitä lokit mukaan (katso ”Missä lokit ovat?” alla). Kerro raportissa sanakirjan ZIP-tiedoston nimi, lähde ja koko. |
| Missä lokit ovat?        | Valitse Ohje -> Avaa lokikansio tai avaa Windowsissa `%USERPROFILE%\.anki_miner\anki_miner.log` tai macOS:ssä/Linuxissa `~/.anki_miner/anki_miner.log`. Kierrätetyt lokit käyttävät päätteitä `.1`–`.5`. Lähetä myös `anki_miner.crash`, jos se on olemassa (sovelluksen kaatanut virhe kirjoittaa pinonsa siihen tiedostoon eikä lokiin), sekä `anki_miner.child.log`, joka sisältää apuprosessin tulosteen. |
| Virheen ilmoittaminen    | Ohje -> Vie diagnostiikka… kirjoittaa valitsemaasi sijaintiin ZIP-tiedoston, joka sisältää lokit (`anki_miner.log` ja sen kierrätetyt versiot, `anki_miner.crash`, `anki_miner.child.log`), `settings.json`-tiedostosi, asetus- ja käyttöliittymän tilatiedostosi, jonojen tilannekuvat ja latausluettelot sekä luodut raportit koneen ja sovelluksen tilasta (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Tarkista se ennen lähettämistä, sillä se sisältää tietokoneesi tiedostopolkuja ja tiedostonimiä. Mitään ei lähetetä automaattisesti. |
| Lisää diagnostiikkalokitusta | Aseta `ANKI_MINER_LOG_LEVEL=DEBUG` ennen Anki Minerin käynnistämistä, niin kolmansien osapuolten yt-dlp-, urllib3- ja fugashi-tiedot tallentuvat. Oletus on `WARNING`; Anki Minerin omat lokit pysyvät DEBUG-tasolla. |
| Ääni on väärällä kielellä | Työkalu valitsee louhintakielen ääniraidan tai muuten ensimmäisen raidan. Valitse raita itse kohdasta Ääniraita… (Video -> Yksittäinen). |
| Tekstitykset eivät ole tahdissa | Käytä käyttöliittymän tekstityksen siirron säädintä (väli ±300 sekuntia). |

## Osallistuminen

Kaikenlaiset osallistumiset ovat tervetulleita.
Jos haluat tukea projektia, jaa se muille, joille siitä voisi olla hyötyä.

- Uusi täällä? Aloita tiedostosta [CONTRIBUTING.md](../CONTRIBUTING.md).
- Arkkitehtuurin yleiskuvaus: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Toimintaohjeet: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Tietoturva: [SECURITY.md](../SECURITY.md).

Virheraportit ja ominaisuuspyynnöt -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Yleiset kysymykset ja keskustelu -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) tai [Discord](https://discord.com/invite/aDtQyZzUVP).

## Erityiskiitokset

Lämpimät kiitokset ihmisille, jotka ovat antaneet projektille poikkeuksellisen panoksen:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Loistavia ominaisuusehdotuksia, uusien julkaisujen testausta, yhteisön rakentamista.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Erinomaisia ominaisuusehdotuksia, yhteisön rakentamista ja moderointia Discordissa.

Katso [CONTRIBUTORS.md](../CONTRIBUTORS.md), jossa luetellaan kaikki, jotka ovat osallistuneet projektiin millä tahansa tavalla.


## Lisenssi

GNU General Public License v3.0. Katso [LICENSE](../LICENSE).
