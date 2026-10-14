<!-- i18n-source: README.md sha256:570dfcaa73bf51ed -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="PyPI-verzió"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Licenc: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="GitHub-letöltések"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="GitHub-csillagok"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord-közösség"></a>
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
<a href="README.fi.md">Suomi</a> ·
<b>Magyar</b> ·
<a href="README.el.md">Ελληνικά</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Japán, kínai, koreai és további huszonkilenc nyelvű eredeti tartalmakból Anki-szókártyák.
</p>

<p align="center">
Androidon is - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner Androidra</a>.
</p>

<p align="center">
Ha az Anki Miner segített, kérjük, adjon neki egy ⭐ csillagot - így mások is könnyebben megtalálják :).
</p>


# <p align="center">Gyűjtési bemutató</p>

![Anki Miner bemutató](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Teljes bemutató hanggal (MP4)</a></p>

### Példakártyák

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (hanggal)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (hanggal)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (hanggal)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Telepítés

### Követelmények

- **Anki** az [AnkiConnect](https://ankiweb.net/shared/info/2055492159) bővítménnyel (kód: `2055492159`)
- **ffmpeg** + **libmpv** (csak a videóelőnézethez) - csak pip/pipx-es vagy forrásból történő telepítésnél szükséges.

Töltse le a platformjának megfelelő fájlt a [legújabb kiadásból](https://github.com/0xzerolight/anki_miner/releases/latest):

| Platform | Letöltés |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (egyéb) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ AVIF képernyőképek és a generált feliratok csendeltávolítása nélkül.

### Megjegyzések az első indításhoz (aláíratlan kiadások)

- **macOS**: nyissa meg a `.dmg` fájlt, húzza az **AnkiMiner** alkalmazást az **Alkalmazások** mappába, majd indítsa el. Az első indítás le lesz tiltva, mert az alkalmazás nincs hitelesítve (notarized): zárja be az ablakot, nyissa meg újra az alkalmazást, és válassza a **Megnyitás mindenképp** lehetőséget (a **Rendszerbeállítások** -> **Adatvédelem és biztonság** alatt is megtalálható). Csak egyszer kell.
- **Linux AppImage**: futtatás előtt tegye futtathatóvá - `chmod +x AnkiMiner-*.AppImage`, vagy a fájlkezelőben **Tulajdonságok** -> **Futtatás engedélyezése**.
- **Windows SmartScreen**: **További információ** -> **Futtatás mindenképpen**.
- **Windows Defender téves riasztás**: állítsa vissza a **Védelmi előzmények** közül, vagy [jelentse a Microsoftnak](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Telepítés PyPI-ról (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

A japánhoz, az indonézhez és a héberhez nem kell semmi más. Más gyűjtési nyelvhez adja hozzá annak motorját:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Az arabhoz és a perzsához nincs külön csomag. Ezek adatai, valamint az európai nyelvek spaCy-modelljei az alkalmazáson belül tölthetők le a Beállítások -> Gyűjtési nyelv oldalon, ahol a fenti letöltések mindent beszereznek.

</details>

<details>
<summary><strong>Telepítés forrásból</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

A teljes fejlesztői környezet beállításáról lásd: [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Lapok

- **Videó** - egyetlen videó–felirat pár, egy kötegelt mappa, YouTube- vagy Bilibili-URL-ek, illetve egy teljes sorozat gyűjtése egyetlen, gyakoriság szerint rangsorolt pakliba.
- **Hangoskönyvek** - hangoskönyvek, podcastok, rádióadások, dalok gyűjtése (hang + felirat/átirat párok).
- **Olvasás** - manga (mokuro), regények (`.epub`, `.txt`; egy könyv vagy egy teljes mappa), önálló feliratfájlok vagy beillesztett szöveg gyűjtése.
- **Statisztika** - gyűjtési előzmények, nehézségi rangsorok, mérföldkövek.
- **Segédeszközök** - feliratok generálása (helyi Whisper), feliratok újraidőzítése (ffsubsync/alass), média sűrítése csak párbeszédet tartalmazó hanggá, videó/hang/felirat letöltése bármely, a yt-dlp által támogatott oldalról, egy kész pakli tanulásra érdemes részének átmásolása új pakliba, meglévő kártyák mezőinek pótlása, mangaoldal-képek OCR-ezése .mokuro fájlokba (mokuro), valamint egy hangoskönyv időzítése a könyv szövegéhez (Könyvszinkron).
- **Beállítások** - minden, ami beállítható.

## További funkciók

- Gyűjtési nyelvek - japán, kínai, koreai, angol, katalán, német, portugál, francia, spanyol, olasz, holland, norvég (bokmål), román, görög, finn, magyar, horvát, svéd, lengyel, litván, dán, török, indonéz, orosz, arab, thai, perzsa, szlovén, ukrán, vietnámi, kantoni és héber, a Beállításokban váltható. A japánon kívüli nyelvek motorja az alkalmazáson belül töltődik le; az indonézhez és a héberhez nem kell motor.
- Szóválogató - minden jelölt szó áttekintése a kártyák létrehozása előtt, a jelenettel, a mangaoldallal és a szótári bejegyzéssel egymás mellett.
- Futtatás visszavonása - egy futtatás által épp létrehozott jegyzetek törlése, közvetlenül az eredményablakból.
- Széles körű szűrés: i+1, gyakorisági rangtartomány, tiltólista, regex, szókészletek és még sok más.
- Offline Yomitan-szótárak importálása - definíciók, hangmagassági hangsúly, gyakoriság - prioritás szerint láncolva.
- Több gyakorisági lista prioritás szerint láncolva.
- Szókiejtés a kártyákon helyi hangcsomagokból, a JapanesePod101-ről, a Google TTS-ből vagy a Microsoft Edge TTS-ből.
- Mondathang az Olvasás kártyáin a Google Fordító TTS-ből, illetve japán és koreai nyelvnél a Naver Papagóból (alapértelmezetten kikapcsolva).
- Szótáranként egyedi glosszáriumstílus, Yomitan-módra.
- Beépített libmpv videóelőnézet - egy szó jelenetének lejátszása válogatás közben, vagy a feliratidőzítés finomhangolása élő lejátszás mellett.
- Animált képernyőképek (lásd a fenti példakártyákat).
- Beállításprofilok - elnevezett konfigurációk mentése és váltás közöttük a fejlécből.
- Gyűjtött kártyák stílusfrissítése - a jelenlegi kártyastílus újbóli alkalmazása a már elkészült kártyákra (Segédeszközök → Kártyapótlás).

<details>
<summary><strong>Beépített témák (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (világos); Frappé, Macchiato, Mocha (sötét)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (világos), Wave (sötét)
- **Rosé Pine** - Dawn (világos); Main, Moon (sötét)
- **Solarized** - Light, Dark
- **Önálló** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

A témák licencei: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Más témát is szeretne? Javasolja egy GitHub Issue-ban.

</details>

<details>
<summary><strong>Hogyan működik</strong></summary>

1. **Beolvassa a feliratokat**, és a szöveget egyes szavakra bontja.
2. **Kiszűri** azokat a tartalmas szavakat, amelyeket még nem ismer - igény szerint a listát a Szóválogatóban saját maga is áttekintheti.
3. **Képernyőképet és hangklipet készít** a videóból minden sorhoz.
4. **Kikeresi a definíciókat** a beállított offline szótárakban.
5. **Elküldi a kész kártyákat az Ankinak.**

</details>

## Ajánlott erőforrások

Szótárak és gyakorisági listák minden gyűjtési nyelvhez, letöltési hivatkozásokkal: [RESOURCES.md](../RESOURCES.md). A Beállítási varázsló a gyűjtési nyelvnek megfelelő készletet kínálja fel.

<details>
<summary><strong>JMnedict-licenc</strong></summary>

A mellékelt névszókészletek a [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) adataiból származnak (JMdict/EDICT projekt, EDRDG, CC BY-SA 4.0).

</details>

## Hibaelhárítás

| Probléma                 | Megoldás                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| „Nem sikerült csatlakozni az Ankihoz” | Indítsa el az Ankit, és ellenőrizze, hogy az AnkiConnect telepítve van-e. |
| „A pakli nem található”  | Válasszon egy meglévő paklit itt: Beállítások -> Kártyák és Anki. A program nem hoz létre paklit; ha újra van szüksége, előbb hozza létre az Ankiban. |
| „A jegyzettípus nem található” | Állítsa be a jegyzettípus mezőneveit itt: Beállítások -> Kártyák és Anki. |
| „Az ffmpeg nem található” | Telepítse az ffmpeget, és adja hozzá a PATH-hoz.                               |
| Nem található definíció  | Adjon hozzá egy Yomitan-szótárat: Beállítások -> Szótárak -> Szótár hozzáadása… |
| A Windows-telepítő nem nyílik meg / SmartScreen-figyelmeztetés | Lásd: [Megjegyzések az első indításhoz](#megjegyzések-az-első-indításhoz-aláíratlan-kiadások): válassza a **További információ** -> **Futtatás mindenképpen** lehetőséget; a Defender téves riasztásait a **Védelmi előzmények** közül állíthatja vissza. |
| Friss telepítés után nincsenek definíciók | Futtassa: Eszközök -> Beállítási varázsló vagy Eszközök -> Ajánlott erőforrások letöltése. Kézi importálásnál hagyja érintetlenül a Yomitan ZIP-fájlt (ne csomagolja ki). |
| A szótár hozzáadása elakad vagy sikertelen | Jegyezze fel az utolsó látható szakaszt, és csatolja a naplókat (lásd lent: „Hol vannak a naplók?”). A jelentésben adja meg a szótár ZIP-fájljának nevét, forrását és méretét. |
| Hol vannak a naplók?     | Használja a Súgó -> Naplómappa megnyitása menüpontot, vagy nyissa meg Windowson a `%USERPROFILE%\.anki_miner\anki_miner.log`, macOS-en/Linuxon a `~/.anki_miner/anki_miner.log` fájlt. A rotált naplók a `.1`–`.5` utótagot kapják. Küldje el az `anki_miner.crash` fájlt is, ha létezik (az alkalmazást leállító összeomlás ebbe a fájlba írja a veremkivonatát, nem a naplóba), valamint az `anki_miner.child.log` fájlt, amely egy segédfolyamat kimenetét tartalmazza. |
| Hiba jelentése           | A Súgó -> Diagnosztika exportálása… egy Ön által választott helyre ír egy ZIP-fájlt, amely tartalmazza a naplókat (`anki_miner.log` és rotált változatai, `anki_miner.crash`, `anki_miner.child.log`), a `settings.json` fájlt, a konfigurációs és felületállapot-fájlokat, a sorok pillanatképeit és a letöltési jegyzékeket, valamint a gép és az alkalmazás állapotáról generált jelentéseket (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Feltöltés előtt nézze át, mert a számítógépéről származó fájlútvonalakat és fájlneveket tartalmaz. Semmi sem töltődik fel automatikusan. |
| Részletesebb diagnosztikai naplózás | Az Anki Miner indítása előtt állítsa be: `ANKI_MINER_LOG_LEVEL=DEBUG`, hogy a külső yt-dlp, urllib3 és fugashi részletei is rögzüljenek. Az alapérték `WARNING`; az Anki Miner saját naplói DEBUG szinten maradnak. |
| A hang rossz nyelvű      | Az eszköz a gyűjtési nyelvű hangsávot választja, ennek hiányában az elsőt. Saját maga is kiválaszthatja a Hangsáv… gombbal (Videó -> Egyedi). |
| A feliratok nincsenek szinkronban | Használja a felület felirateltolás-vezérlőjét (tartomány: ±300 másodperc). |

## Közreműködés

Bármilyen közreműködést szívesen fogadunk.
Ha támogatni szeretné a projektet, ossza meg másokkal is, akiknek hasznos lehet.

- Új itt? Kezdje a [CONTRIBUTING.md](../CONTRIBUTING.md) fájllal.
- Architektúra-áttekintés: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Magatartási kódex: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Biztonság: [SECURITY.md](../SECURITY.md).

Hibajelentések és funkciókérések -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Általános kérdések és beszélgetés -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) vagy [Discord](https://discord.com/invite/aDtQyZzUVP).

## Külön köszönet

Őszinte köszönet azoknak, akik kiemelkedően hozzájárultak a projekthez:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Kiváló funkciójavaslatok, új kiadások tesztelése, közösségépítés.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Remek funkciójavaslatok, közösségépítés és moderálás a Discordon.

A projekthez bármilyen módon hozzájárulók listája: [CONTRIBUTORS.md](../CONTRIBUTORS.md).


## Licenc

GNU General Public License v3.0. Lásd: [LICENSE](../LICENSE).
