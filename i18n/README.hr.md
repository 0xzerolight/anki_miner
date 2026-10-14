<!-- i18n-source: README.md sha256:4af87aba1e429cf1 -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="Verzija na PyPI-ju"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Licenca: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="Preuzimanja s GitHuba"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="Zvjezdice na GitHubu"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord zajednica"></a>
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
<b>Hrvatski</b> ·
<a href="README.lt.md">Lietuvių</a> ·
<a href="README.fi.md">Suomi</a> ·
<a href="README.hu.md">Magyar</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Pretvorite izvorni sadržaj na japanskom, kineskom, korejskom i još dvadeset devet jezika u Anki kartice s vokabularom.
</p>

<p align="center">
Dostupno i za Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner za Android</a>.
</p>

<p align="center">
Ako vam je Anki Miner pomogao, ostavite ⭐ zvjezdicu - tako ga i drugi lakše pronađu :).
</p>


# <p align="center">Demonstracija prikupljanja</p>

![Prikaz programa Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Cijela demonstracija sa zvukom (MP4)</a></p>

### Primjeri kartica

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (zvuk)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (zvuk)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (zvuk)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Instalacija

### Preduvjeti

- **Anki** s dodatkom [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (kod `2055492159`)
- **ffmpeg** + **libmpv** (samo za pregled videozapisa) - potrebni samo pri instalaciji putem pip/pipx ili iz izvornog koda.

Preuzmite datoteku za svoju platformu iz [najnovijeg izdanja](https://github.com/0xzerolight/anki_miner/releases/latest):

| Platforma | Preuzimanje |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (ostali) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Bez AVIF snimki zaslona i uklanjanja tišine za generirane titlove.

### Napomene za prvo pokretanje (nepotpisane verzije)

- **macOS**: otvorite `.dmg`, povucite **AnkiMiner** u **Applications**, a zatim ga pokrenite. Prvo pokretanje je blokirano jer aplikacija nije notarizirana: zatvorite dijaloški okvir, ponovno otvorite aplikaciju i odaberite **Open Anyway** (također pod **System Settings** -> **Privacy & Security**). Samo jednom.
- **Linux AppImage**: prije pokretanja učinite datoteku izvršnom - `chmod +x AnkiMiner-*.AppImage` ili **Svojstva** -> **Dopusti izvršavanje** u upravitelju datoteka.
- **Windows SmartScreen**: **Više informacija** -> **Ipak pokreni**.
- **Lažno pozitivan nalaz programa Windows Defender**: vratite datoteku iz **Povijesti zaštite** ili [prijavite Microsoftu](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Instalacija s PyPI-ja (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Japanski, indonezijski i hebrejski ne trebaju ništa dodatno. Za drugi jezik prikupljanja dodajte njegov mehanizam:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Arapski i perzijski nemaju dodatak. Njihovi podaci i spaCy modeli europskih jezika preuzimaju se u aplikaciji u odjeljku Postavke -> Jezik prikupljanja, gdje gornja preuzimanja dohvaćaju sve.

</details>

<details>
<summary><strong>Instalacija iz izvornog koda</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Za potpuno razvojno okruženje pogledajte [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Kartice

- **Video** - prikupljajte iz jednog para videozapisa i titlova, skupne mape, URL-ova s YouTubea ili Bilibilija ili cijele serije u jedan špil rangiran po učestalosti.
- **Audioknjige** - prikupljajte iz audioknjiga, podcasta, radija i pjesama (parovi zvuka i titlova/transkripta).
- **Čitanje** - prikupljajte iz mangi (mokuro), romana (`.epub`, `.txt`; jedna knjiga ili cijela mapa), samostalnih datoteka titlova ili zalijepljenog teksta.
- **Analitika** - povijest prikupljanja, ljestvice težine, prekretnice.
- **Pomagala** - generiranje titlova (lokalni Whisper), poravnanje titlova (ffsubsync/alass), sažimanje medija na zvuk samo s dijalogom, preuzimanje videozapisa, zvuka i titlova s bilo koje stranice koju yt-dlp podržava, kopiranje dijela gotovog špila vrijednog učenja u novi špil, dopuna polja na postojećim karticama, OCR slika stranica mange u datoteke .mokuro (mokuro) i vremensko usklađivanje audioknjige s tekstom knjige (Sinkronizacija).
- **Postavke** - sve što se može konfigurirati.

## Ostale značajke

- Jezici prikupljanja - japanski, kineski, korejski, engleski, katalonski, njemački, portugalski, francuski, španjolski, talijanski, nizozemski, norveški bokmål, rumunjski, grčki, finski, mađarski, hrvatski, švedski, poljski, litavski, danski, turski, indonezijski, ruski, arapski, tajlandski, perzijski, slovenski, ukrajinski, vijetnamski, kantonski i hebrejski, s promjenom u Postavkama. Jezici osim japanskog preuzimaju svoj mehanizam u aplikaciji; indonezijski i hebrejski ga ne trebaju.
- Kustos riječi - pregledajte svaku kandidatsku riječ prije izrade kartica, uz njezinu scenu, stranicu mange i unos u rječniku jedno pokraj drugog.
- Poništavanje pokretanja - izbrišite bilješke koje je pokretanje upravo stvorilo, izravno iz dijaloškog okvira s rezultatima.
- Opsežno filtriranje: i+1, raspon ranga učestalosti, crna lista, regularni izrazi, skupovi riječi i još mnogo toga.
- Uvoz izvanmrežnih Yomitan rječnika - definicije, tonski naglasak, učestalost - povezanih u lanac po prioritetu.
- Više popisa učestalosti povezanih u lanac po prioritetu.
- Zvuk riječi na karticama iz lokalnih zvučnih paketa, JapanesePod101, Google TTS-a ili Microsoft Edge TTS-a.
- Zvuk rečenice na karticama iz Čitanja putem Google Translate TTS-a ili Naver Papaga za japanski i korejski (zadano isključeno).
- Stiliziranje pojmovnika po rječniku, u stilu Yomitana.
- Ugrađeni libmpv pregled videozapisa - reproducirajte scenu riječi tijekom odabira ili fino podesite vrijeme titlova uz reprodukciju uživo.
- Animirane snimke zaslona (pogledajte primjere kartica iznad).
- Profili postavki - spremite imenovane konfiguracije i prebacujte se između njih iz zaglavlja.
- Preoblikuj prikupljene kartice - ponovno primijenite trenutačni stil kartica na kartice koje ste već izradili (Pomagala → Dopuna kartica).

<details>
<summary><strong>Ugrađene teme (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (svijetla); Frappé, Macchiato, Mocha (tamne)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (svijetla), Wave (tamna)
- **Rosé Pine** - Dawn (svijetla); Main, Moon (tamne)
- **Solarized** - Light, Dark
- **Samostalne** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Licence tema: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Želite li da se doda još neka tema? Predložite je u GitHub Issueu.

</details>

<details>
<summary><strong>Kako radi</strong></summary>

1. **Čita titlove** i dijeli tekst na pojedinačne riječi.
2. **Izdvaja** samo sadržajne riječi koje još ne znate - uz mogućnost da sami pregledate popis u Kustosu riječi.
3. **Uzima snimku zaslona i zvučni isječak** iz videozapisa za svaki redak.
4. **Traži definicije** u konfiguriranim izvanmrežnim rječnicima.
5. **Šalje gotove kartice u Anki.**

</details>

## Preporučeni resursi

Rječnici i popisi učestalosti za svaki jezik prikupljanja, s poveznicama za preuzimanje: [RESOURCES.md](../RESOURCES.md). Čarobnjak za postavljanje nudi pravi skup za vaš jezik prikupljanja.

<details>
<summary><strong>Licenca za JMnedict</strong></summary>

Koristi ugrađene skupove imena izvedene iz [JMnedicta](https://www.edrdg.org/enamdict/enamdict_doc.html) (projekt JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Rješavanje problema

| Problem                  | Rješenje                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| "Nije moguće povezati se s Ankijem" | Pokrenite Anki i provjerite je li AnkiConnect instaliran.                        |
| "Špil nije pronađen"     | Odaberite postojeći špil u odjeljku Postavke -> Kartice i Anki. Špilovi se ne stvaraju automatski; ako vam treba novi, najprije ga izradite u Ankiju. |
| "Vrsta bilješke nije pronađena" | Konfigurirajte nazive polja svoje vrste bilješke u odjeljku Postavke -> Kartice i Anki.    |
| "ffmpeg nije pronađen"   | Instalirajte ffmpeg i dodajte ga u PATH.                                         |
| Nisu pronađene definicije | Dodajte Yomitan rječnik: Postavke -> Rječnici -> Dodaj rječnik… |
| Instalacijski program za Windows ne otvara se / upozorenje SmartScreena | Pogledajte [Napomene za prvo pokretanje](#napomene-za-prvo-pokretanje-nepotpisane-verzije): odaberite **Više informacija** -> **Ipak pokreni**; lažno pozitivne nalaze programa Defender vratite iz **Povijesti zaštite**. |
| Nova instalacija nema definicija | Pokrenite Alati -> Čarobnjak za postavljanje ili Alati -> Preuzmi preporučene resurse. Za ručni uvoz ostavite Yomitan ZIP netaknutim (nemojte ga raspakirati). |
| Dodavanje rječnika zastaje ili ne uspijeva | Zabilježite posljednju vidljivu fazu i priložite zapisnike (pogledajte "Gdje su zapisnici?" u nastavku). U prijavu uključite naziv, izvor i veličinu ZIP datoteke rječnika. |
| Gdje su zapisnici?       | Upotrijebite Pomoć -> Otvori mapu zapisnika ili otvorite `%USERPROFILE%\.anki_miner\anki_miner.log` na Windowsima odnosno `~/.anki_miner/anki_miner.log` na macOS-u/Linuxu. Rotirani zapisnici imaju nastavke od `.1` do `.5`. Pošaljite i `anki_miner.crash` ako postoji (rušenje koje je srušilo aplikaciju zapisuje svoj stog u tu datoteku, a ne u zapisnik) te `anki_miner.child.log`, koji sadrži izlaz pomoćnog procesa. |
| Prijava pogreške         | Pomoć -> Izvezi dijagnostiku… zapisuje ZIP na mjesto koje odaberete, a sadrži zapisnike (`anki_miner.log` i njegove rotacije, `anki_miner.crash`, `anki_miner.child.log`), vaš `settings.json`, vaše datoteke konfiguracije i stanja sučelja, snimke reda čekanja i manifeste preuzimanja te generirana izvješća o stanju računala i aplikacije (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Pregledajte ga prije slanja jer sadrži putanje i nazive datoteka s vašeg računala. Ništa se ne šalje automatski. |
| Detaljniji dijagnostički zapisnici | Postavite `ANKI_MINER_LOG_LEVEL=DEBUG` prije pokretanja programa Anki Miner kako biste zabilježili pojedinosti vanjskih biblioteka yt-dlp, urllib3 i fugashi. Zadana vrijednost je `WARNING`; zapisnici programa Anki Miner ostaju na razini DEBUG. |
| Zvuk je na pogrešnom jeziku | Alat odabire zvučni zapis jezika prikupljanja, a inače prvi zapis. Sami ga odaberite gumbom Zvučni zapis… (Video -> Pojedinačno). |
| Titlovi nisu usklađeni   | Upotrijebite kontrolu pomaka titlova u sučelju (raspon ±300 sekundi).            |

## Doprinos

Dobrodošli su doprinosi svih vrsta.
Ako želite podržati projekt, podijelite ga s drugima kojima bi mogao koristiti.

- Novi ste ovdje? Počnite s [CONTRIBUTING.md](../CONTRIBUTING.md).
- Pregled arhitekture: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Kodeks ponašanja: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Sigurnost: [SECURITY.md](../SECURITY.md).

Prijave pogrešaka i prijedlozi značajki -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Opća pitanja i rasprave -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) ili [Discord](https://discord.com/invite/aDtQyZzUVP).

## Posebne zahvale

Iskrene zahvale ljudima koji su iznimno pridonijeli projektu:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Izvrsni prijedlozi značajki, testiranje novih izdanja, izgradnja zajednice.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Izvrsni prijedlozi značajki, izgradnja zajednice i moderiranje na Discordu.

Pogledajte [CONTRIBUTORS.md](../CONTRIBUTORS.md) za sve koji su na bilo koji način pridonijeli projektu.


## Licenca

GNU General Public License v3.0. Pogledajte [LICENSE](../LICENSE).
