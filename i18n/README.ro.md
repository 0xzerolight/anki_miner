<!-- i18n-source: README.md sha256:6f9163baa7a87e06 -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="Versiune PyPI"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Licență: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="Descărcări GitHub"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="Stele GitHub"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Comunitatea Discord"></a>
</p>

<!-- i18n-nav:start -->
<div align="center">
<details>
<summary>Limbă: Română</summary>
<p>
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
<a href="README.hu.md">Magyar</a> ·
<a href="README.el.md">Ελληνικά</a> ·
<b>Română</b> ·
<a href="README.tr.md">Türkçe</a>
</p>
</details>
</div>
<!-- i18n-nav:end -->

<p align="center">
Transformați conținut autentic în japoneză, chineză, coreeană și alte douăzeci și nouă de limbi în carduri Anki de vocabular.
</p>

<p align="center">
Disponibil și pe Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner pentru Android</a>.
</p>

<p align="center">
Dacă Anki Miner v-a fost de folos, lăsați o ⭐ stea - îi ajută și pe alții să-l găsească :).
</p>


# <p align="center">Demonstrație de extragere</p>

![Prezentare Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Demonstrația completă cu sunet (MP4)</a></p>

### Exemple de carduri

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (cu sunet)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (cu sunet)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (cu sunet)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Instalare

### Cerințe

- **Anki** cu suplimentul [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (cod `2055492159`)
- **ffmpeg** + **libmpv** (doar pentru previzualizarea video) - necesare doar la instalarea prin pip/pipx sau din sursă.

Descărcați fișierul pentru platforma dumneavoastră din [cea mai recentă versiune](https://github.com/0xzerolight/anki_miner/releases/latest):

| Platformă | Descărcare |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (altele) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Fără capturi de ecran AVIF și fără eliminarea liniștii la subtitrările generate.

### Note pentru prima rulare (versiuni nesemnate)

- **macOS**: deschideți fișierul `.dmg`, trageți **AnkiMiner** în **Aplicații**, apoi lansați-l. Prima lansare este blocată deoarece aplicația nu este notarizată: închideți dialogul, deschideți din nou aplicația și alegeți **Deschide oricum** (disponibil și în **Configurări sistem** -> **Confidențialitate și securitate**). O singură dată.
- **Linux AppImage**: faceți fișierul executabil înainte de a-l rula - `chmod +x AnkiMiner-*.AppImage` sau **Proprietăți** -> **Permite executarea** în managerul de fișiere.
- **Windows SmartScreen**: **Mai multe informații** -> **Executați oricum**.
- **Fals pozitiv Windows Defender**: restaurați din **Istoricul protecției** sau [raportați la Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Instalare din PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Japoneza, indoneziana și ebraica nu necesită nimic în plus. Pentru altă limbă de extragere, adăugați motorul ei:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Araba și persana nu au un extra. Datele lor, precum și modelele spaCy ale limbilor europene, se descarcă din aplicație, din Setări -> Limba de extragere, unde descărcările de mai sus aduc totul.

</details>

<details>
<summary><strong>Instalare din sursă</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Pentru configurarea completă a mediului de dezvoltare, consultați [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## File

- **Video** - extrageți dintr-o singură pereche video/subtitrare, dintr-un dosar în lot, din URL-uri YouTube sau Bilibili ori dintr-un serial întreg într-un singur pachet ordonat după frecvență.
- **Cărți audio** - extrageți din cărți audio, podcasturi, radio, cântece (perechi audio + subtitrare/transcriere).
- **Lectură** - extrageți din manga (mokuro), romane (`.epub`, `.txt`; o singură carte sau un dosar întreg), fișiere de subtitrare independente sau text lipit.
- **Statistici** - istoricul extragerilor, clasamente de dificultate, repere.
- **Utilitare** - generați subtitrări (Whisper local), resincronizați subtitrări (ffsubsync/alass), condensați fișiere media în audio doar cu dialog, descărcați video/audio/subtitrări de pe orice site acceptat de yt-dlp, copiați partea care merită învățată dintr-un pachet gata făcut într-unul nou, completați câmpuri pe cardurile existente, aplicați OCR pe imaginile paginilor manga pentru a obține fișiere .mokuro (mokuro) și sincronizați o carte audio cu textul cărții (Sincronizare carte).
- **Setări** - tot ce se poate configura.

## Alte funcții

- Limbi de extragere - japoneză, chineză, coreeană, engleză, catalană, germană, portugheză, franceză, spaniolă, italiană, neerlandeză, norvegiană bokmål, română, greacă, finlandeză, maghiară, croată, suedeză, poloneză, lituaniană, daneză, turcă, indoneziană, rusă, arabă, thailandeză, persană, slovenă, ucraineană, vietnameză, cantoneză și ebraică, comutate din Setări. Limbile în afară de japoneză își descarcă motorul din aplicație; indoneziana și ebraica nu au nevoie de unul.
- Selector de cuvinte - revizuiți fiecare cuvânt candidat înainte de crearea cardurilor, cu scena, pagina de manga și intrarea din dicționar una lângă alta.
- Anularea unei rulări - ștergeți notele create de o rulare direct din dialogul ei de rezultate.
- Filtrare extinsă: i+1, interval de rang de frecvență, listă neagră, regex, seturi de cuvinte și altele.
- Import offline de dicționare Yomitan - definiții, accent tonal, frecvență - înlănțuite după prioritate.
- Mai multe liste de frecvență înlănțuite după prioritate.
- Audio pentru cuvinte pe carduri din pachete audio locale, JapanesePod101, Google TTS sau Microsoft Edge TTS.
- Audio pentru propoziții pe cardurile din Lectură, din Google Translate TTS sau Naver Papago pentru japoneză și coreeană (dezactivat implicit).
- Stilizarea glosarului pentru fiecare dicționar, în stilul Yomitan.
- Previzualizare video libmpv încorporată - redați scena unui cuvânt în timpul selecției sau ajustați sincronizarea subtitrărilor cu redare în timp real.
- Capturi de ecran animate (vedeți exemplele de carduri de mai sus).
- Profiluri de setări - salvați configurații cu nume și comutați între ele din antet.
- Restilizează cardurile extrase - reaplicați stilul actual al cardurilor pe cardurile deja create; pentru cardurile din versiunile mai vechi folosiți în schimb Completare carduri (Utilitare → Completare carduri).

<details>
<summary><strong>Teme încorporate (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (luminoasă); Frappé, Macchiato, Mocha (întunecate)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (luminoasă), Wave (întunecată)
- **Rosé Pine** - Dawn (luminoasă); Main, Moon (întunecate)
- **Solarized** - Light, Dark
- **Independente** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Licențele temelor: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Doriți să fie adăugată altă temă? Propuneți-o într-un GitHub Issue.

</details>

<details>
<summary><strong>Cum funcționează</strong></summary>

1. **Citește subtitrările** și împarte textul în cuvinte individuale.
2. **Filtrează** cuvintele cu sens pe care nu le cunoașteți încă - opțional, puteți revizui lista în Selectorul de cuvinte.
3. **Preia o captură de ecran și un clip audio** din video pentru fiecare linie.
4. **Caută definițiile** în dicționarele offline configurate.
5. **Trimite cardurile finalizate în Anki.**

</details>

## Resurse recomandate

Dicționare și liste de frecvență pentru fiecare limbă de extragere, cu linkuri de descărcare: [RESOURCES.md](../RESOURCES.md). Asistentul de configurare oferă setul potrivit pentru limba de extragere.

<details>
<summary><strong>Licența JMnedict</strong></summary>

Folosește seturi de nume incluse, derivate din [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (proiectul JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Depanare

| Problemă                 | Soluție                                                                          |
|--------------------------|----------------------------------------------------------------------------------|
| „Cannot connect to Anki” | Porniți Anki și asigurați-vă că AnkiConnect este instalat.                       |
| „Deck not found”         | Alegeți un pachet existent în Setări -> Carduri și Anki. Pachetele nu sunt create automat; creați-l mai întâi în Anki dacă aveți nevoie de unul nou. |
| „Note type not found”    | Configurați numele câmpurilor tipului de notă în Setări -> Carduri și Anki.       |
| „ffmpeg not found”       | Instalați ffmpeg și adăugați-l în PATH.                                          |
| Nu s-au găsit definiții  | Adăugați un dicționar Yomitan în Setări -> Dicționare -> Adaugă dicționar… |
| Programul de instalare Windows nu se deschide / avertisment SmartScreen | Vedeți [Note pentru prima rulare](#note-pentru-prima-rulare-versiuni-nesemnate): selectați **Mai multe informații** -> **Executați oricum**; restaurați falsurile pozitive Defender din **Istoricul protecției**. |
| O instalare nouă nu are definiții | Rulați Instrumente -> Asistent de configurare sau Instrumente -> Descarcă resursele recomandate. Pentru importul manual, păstrați arhiva ZIP Yomitan intactă (nu o dezarhivați). |
| Adăugarea dicționarului se blochează sau eșuează | Notați ultima etapă vizibilă și atașați jurnalele (vedeți „Unde sunt jurnalele?” mai jos). Includeți în raport numele arhivei ZIP a dicționarului, sursa și dimensiunea. |
| Unde sunt jurnalele?     | Folosiți Ajutor -> Deschide dosarul jurnalelor sau deschideți `%USERPROFILE%\.anki_miner\anki_miner.log` pe Windows ori `~/.anki_miner/anki_miner.log` pe macOS/Linux. Jurnalele rotite folosesc sufixele `.1` până la `.5`. Trimiteți și `anki_miner.crash` dacă există (o eroare fatală care a închis aplicația își scrie stiva în acel fișier, nu în jurnal), precum și `anki_miner.child.log`, care conține ieșirea unui proces auxiliar. |
| Raportarea unei erori    | Ajutor -> Exportă diagnosticul… scrie o arhivă ZIP într-o locație aleasă de dumneavoastră, cu jurnalele (`anki_miner.log` și rotațiile sale, `anki_miner.crash`, `anki_miner.child.log`), fișierul `settings.json`, fișierele de configurare și de stare a interfeței, instantanee ale cozilor și manifeste de descărcare, plus rapoarte generate despre starea computerului și a aplicației (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Verificați arhiva înainte de a o încărca, deoarece conține căi și nume de fișiere de pe computerul dumneavoastră. Nimic nu este încărcat automat. |
| Jurnalizare de diagnostic mai detaliată | Setați `ANKI_MINER_LOG_LEVEL=DEBUG` înainte de a porni Anki Miner pentru a capta detaliile componentelor terțe yt-dlp, urllib3 și fugashi. Valoarea implicită este `WARNING`; jurnalele Anki Miner rămân la DEBUG. |
| Audio în limba greșită   | Instrumentul alege pista audio în limba de extragere, altfel prima pistă. Alegeți-o manual cu Pistă audio… (Video -> Individual). |
| Subtitrări desincronizate | Folosiți controlul de decalaj al subtitrării din interfață (interval ±300 de secunde). |

## Contribuții

Contribuțiile de orice fel sunt binevenite.
Dacă doriți să susțineți proiectul, împărtășiți-l cu alții cărora le-ar putea fi util.

- Sunteți nou aici? Începeți cu [CONTRIBUTING.md](../CONTRIBUTING.md).
- Prezentare generală a arhitecturii: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Codul de conduită: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Securitate: [SECURITY.md](../SECURITY.md).

Rapoarte de erori și cereri de funcții -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Întrebări generale și discuții -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) sau [Discord](https://discord.com/invite/aDtQyZzUVP).

## Mulțumiri speciale

Mulțumiri sincere persoanelor care au adus contribuții excepționale proiectului:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Sugestii excelente de funcții, testarea versiunilor noi, construirea comunității.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Sugestii excelente de funcții, construirea și moderarea comunității pe Discord.

Consultați [CONTRIBUTORS.md](../CONTRIBUTORS.md) pentru toți cei care au contribuit în orice fel la proiect.


## Licență

GNU General Public License v3.0. Consultați [LICENSE](../LICENSE).
