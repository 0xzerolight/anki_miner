<!-- i18n-source: README.md sha256:e349c147c5d08d7d -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="Wersja w PyPI"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Licencja: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="Pobrania z GitHub"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="Gwiazdki na GitHub"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Społeczność na Discord"></a>
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
<b>Polski</b> ·
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
Zamień materiały w oryginale po japońsku, chińsku, koreańsku i w dwudziestu dziewięciu innych językach w karty Anki ze słownictwem.
</p>

<p align="center">
Dostępny także na Androida - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner na Androida</a>.
</p>

<p align="center">
Jeśli Anki Miner Ci pomógł, zostaw ⭐ gwiazdkę - dzięki temu inni łatwiej go znajdą :).
</p>


# <p align="center">Demo zbierania</p>

![Prezentacja Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Pełne demo z dźwiękiem (MP4)</a></p>

### Przykładowe karty

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (z dźwiękiem)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (z dźwiękiem)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (z dźwiękiem)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Instalacja

### Wymagania

- **Anki** z dodatkiem [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (kod `2055492159`)
- **ffmpeg** + **libmpv** (tylko do podglądu wideo) - potrzebne wyłącznie przy instalacji przez pip/pipx lub ze źródeł.

Pobierz plik dla swojej platformy z [najnowszego wydania](https://github.com/0xzerolight/anki_miner/releases/latest):

| Platforma | Plik do pobrania |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (inne) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Bez zrzutów ekranu AVIF i bez usuwania ciszy w generowanych napisach.

### Uwagi do pierwszego uruchomienia (niepodpisane kompilacje)

- **macOS**: otwórz plik `.dmg`, przeciągnij **AnkiMiner** do **Applications**, a potem go uruchom. Pierwsze uruchomienie zostanie zablokowane, bo aplikacja nie jest notaryzowana: zamknij okno dialogowe, otwórz aplikację ponownie i wybierz **Otwórz mimo to** (także w oknie **Ustawienia systemowe** -> **Prywatność i ochrona**). Tylko raz.
- **Linux AppImage**: przed uruchomieniem nadaj plikowi prawo wykonywania - `chmod +x AnkiMiner-*.AppImage` albo **Właściwości** -> **Zezwól na wykonywanie** w menedżerze plików.
- **Windows SmartScreen**: **Więcej informacji** -> **Uruchom mimo to**.
- **Fałszywy alarm Windows Defender**: przywróć plik z **Historii ochrony** lub [zgłoś go Microsoftowi](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Instalacja z PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Japoński, indonezyjski i hebrajski nie wymagają niczego dodatkowo. Dla innego języka nauki dodaj jego silnik:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Arabski i perski nie mają dodatku. Ich dane, a także modele spaCy języków europejskich, pobiera się w aplikacji w zakładce Ustawienia -> Język nauki, gdzie wersje do pobrania wymienione wyżej pobierają wszystko.

</details>

<details>
<summary><strong>Instalacja ze źródeł</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Pełną konfigurację środowiska programistycznego opisuje [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Zakładki

- **Wideo** - zbieranie z pojedynczej pary wideo/napisy, folderu wsadowego, adresów URL YouTube lub Bilibili albo z całej serii do jednej talii uporządkowanej według frekwencji.
- **Audiobooki** - zbieranie z audiobooków, podcastów, radia i piosenek (pary audio + napisy/transkrypcja).
- **Czytanie** - zbieranie z mangi (mokuro), powieści (`.epub`, `.txt`; jedna książka lub cały folder), samodzielnych plików napisów lub wklejonego tekstu.
- **Analityka** - historia zbierania, rankingi trudności, kamienie milowe.
- **Przybornik** - generowanie napisów (lokalny Whisper), korekta czasu napisów (ffsubsync/alass), kondensowanie mediów do audio z samymi dialogami, pobieranie wideo/audio/napisów z każdej witryny obsługiwanej przez yt-dlp, kopiowanie wartej nauki części gotowej talii do nowej, uzupełnianie pól w istniejących kartach, OCR obrazów stron mangi do plików .mokuro (mokuro) oraz synchronizacja audiobooka z tekstem książki (Synchr. audiobooka).
- **Ustawienia** - wszystko, co można skonfigurować.

## Inne funkcje

- Języki nauki - japoński, chiński, koreański, angielski, kataloński, niemiecki, portugalski, francuski, hiszpański, włoski, niderlandzki, norweski (bokmål), rumuński, grecki, fiński, węgierski, chorwacki, szwedzki, polski, litewski, duński, turecki, indonezyjski, rosyjski, arabski, tajski, perski, słoweński, ukraiński, wietnamski, kantoński i hebrajski, przełączane w Ustawieniach. Języki inne niż japoński pobierają swój silnik w aplikacji; indonezyjski i hebrajski go nie potrzebują.
- Kurator słów - przegląd każdego kandydata przed utworzeniem kart, ze sceną, stroną mangi i hasłem słownikowym obok siebie.
- Cofnięcie przebiegu - usunięcie notatek utworzonych przez przebieg prosto z okna jego wyników.
- Rozbudowane filtrowanie: i+1, zakres pozycji frekwencji, czarna lista, wyrażenia regularne, zbiory słów i inne.
- Import słowników Yomitan offline - definicje, akcent toniczny, frekwencja - połączone w łańcuch według priorytetu.
- Wiele list frekwencji połączonych w łańcuch według priorytetu.
- Audio słów na kartach z lokalnych pakietów audio, JapanesePod101, Google TTS lub Microsoft Edge TTS.
- Audio zdań na kartach z Czytania z Google Translate TTS albo Naver Papago dla japońskiego i koreańskiego (domyślnie wyłączone).
- Styl glosariusza osobny dla każdego słownika, jak w Yomitan.
- Wbudowany podgląd wideo libmpv - odtwarzanie sceny słowa podczas wyboru słów lub korekta czasu napisów z podglądem na żywo.
- Animowane zrzuty ekranu (zobacz przykładowe karty powyżej).
- Profile ustawień - zapisywanie nazwanych konfiguracji i przełączanie między nimi z nagłówka.
- Odśwież styl zebranych kart - ponowne zastosowanie bieżącego stylu kart do kart już utworzonych (Przybornik → Uzupełnij karty).

<details>
<summary><strong>Wbudowane motywy (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (jasny); Frappé, Macchiato, Mocha (ciemne)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (jasny), Wave (ciemny)
- **Rosé Pine** - Dawn (jasny); Main, Moon (ciemne)
- **Solarized** - Light, Dark
- **Samodzielne** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Licencje motywów: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Chcesz, żeby pojawił się inny motyw? Zaproponuj go w zgłoszeniu na GitHub.

</details>

<details>
<summary><strong>Jak to działa</strong></summary>

1. **Odczytuje napisy** i dzieli tekst na pojedyncze słowa.
2. **Filtruje** do słów znaczeniowych, których jeszcze nie znasz - opcjonalnie możesz sam przejrzeć listę w Kuratorze słów.
3. **Pobiera zrzut ekranu i klip audio** z wideo dla każdego wiersza.
4. **Wyszukuje definicje** w skonfigurowanych słownikach offline.
5. **Wysyła gotowe karty do Anki.**

</details>

## Zalecane zasoby

Słowniki i listy frekwencji dla każdego języka nauki, z linkami do pobrania: [RESOURCES.md](../RESOURCES.md). Kreator konfiguracji proponuje zestaw odpowiedni dla Twojego języka nauki.

<details>
<summary><strong>Licencja JMnedict</strong></summary>

Korzysta z dołączonych zbiorów nazw własnych opartych na [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (projekt JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Rozwiązywanie problemów

| Problem                    | Rozwiązanie                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| „Nie można połączyć się z Anki” | Uruchom Anki i upewnij się, że AnkiConnect jest zainstalowany.                                  |
| „Nie znaleziono talii”         | Wybierz istniejącą talię w zakładce Ustawienia -> Karty i Anki. Talie nie są tworzone automatycznie; jeśli potrzebujesz nowej, najpierw utwórz ją w Anki. |
| „Nie znaleziono typu notatki”    | Skonfiguruj nazwy pól swojego typu notatki w zakładce Ustawienia -> Karty i Anki.               |
| „Nie znaleziono ffmpeg”       | Zainstaluj ffmpeg i dodaj go do PATH.                                               |
| Nie znaleziono definicji     | Dodaj słownik Yomitan w zakładce Ustawienia -> Słowniki -> Dodaj słownik… |
| Instalator Windows się nie otwiera / ostrzeżenie SmartScreen | Zobacz [Uwagi do pierwszego uruchomienia](#uwagi-do-pierwszego-uruchomienia-niepodpisane-kompilacje): wybierz **Więcej informacji** -> **Uruchom mimo to**; fałszywe alarmy Defendera przywróć z **Historii ochrony**. |
| Świeża instalacja nie ma definicji | Uruchom Narzędzia -> Kreator konfiguracji lub Narzędzia -> Pobierz zalecane zasoby. Przy ręcznym imporcie zostaw plik ZIP Yomitan nienaruszony (nie rozpakowuj go). |
| Dodawanie słownika zawiesza się lub kończy błędem | Zanotuj ostatni widoczny etap i dołącz dzienniki (zobacz „Gdzie są dzienniki?” poniżej). W zgłoszeniu podaj nazwę pliku ZIP słownika, jego źródło i rozmiar. |
| Gdzie są dzienniki?      | Użyj polecenia Pomoc -> Otwórz folder dziennika albo otwórz `%USERPROFILE%\.anki_miner\anki_miner.log` w Windows lub `~/.anki_miner/anki_miner.log` w macOS/Linux. Starsze dzienniki mają przyrostki od `.1` do `.5`. Wyślij też `anki_miner.crash`, jeśli istnieje (awaria, która zamknęła aplikację, zapisuje stos wywołań do tego pliku, a nie do dziennika), oraz `anki_miner.child.log` z wyjściem procesu pomocniczego. |
| Zgłaszanie błędu          | Pomoc -> Eksportuj diagnostykę… zapisuje w wybranym miejscu plik ZIP z dziennikami (`anki_miner.log` i jego rotacje, `anki_miner.crash`, `anki_miner.child.log`), Twoim `settings.json`, plikami konfiguracji i stanu interfejsu, migawkami kolejek i manifestami pobierania oraz wygenerowanymi raportami stanu komputera i aplikacji (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Przejrzyj go przed wysłaniem, bo zawiera ścieżki i nazwy plików z Twojego komputera. Nic nie jest wysyłane automatycznie. |
| Więcej informacji diagnostycznych | Ustaw `ANKI_MINER_LOG_LEVEL=DEBUG` przed uruchomieniem Anki Miner, aby zapisywać szczegóły z zewnętrznych bibliotek yt-dlp, urllib3 i fugashi. Domyślna wartość to `WARNING`; dzienniki samego Anki Miner pozostają na poziomie DEBUG. |
| Audio w złym języku  | Narzędzie wybiera ścieżkę audio w języku nauki, a w przeciwnym razie pierwszą. Wybierz ją sam przyciskiem Ścieżka audio… (Wideo -> Pojedynczo). |
| Napisy nie są zsynchronizowane    | Użyj ustawienia przesunięcia napisów w interfejsie (zakres ±300 sekund).                 |

## Współtworzenie

Mile widziany jest każdy rodzaj wkładu.
Jeśli chcesz wesprzeć projekt, poleć go innym, którym może się przydać.

- Jesteś tu nowy? Zacznij od [CONTRIBUTING.md](../CONTRIBUTING.md).
- Przegląd architektury: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Kodeks postępowania: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Bezpieczeństwo: [SECURITY.md](../SECURITY.md).

Zgłoszenia błędów i propozycje funkcji -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Ogólne pytania i dyskusje -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) lub [Discord](https://discord.com/invite/aDtQyZzUVP).

## Specjalne podziękowania

Serdeczne podziękowania dla osób, które wniosły do projektu wyjątkowy wkład:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Świetne propozycje funkcji, testowanie nowych wydań, budowanie społeczności.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Doskonałe propozycje funkcji, budowanie społeczności i moderacja na Discordzie.

Wszystkie osoby, które w jakikolwiek sposób przyczyniły się do projektu, wymienia [CONTRIBUTORS.md](../CONTRIBUTORS.md).


## Licencja

GNU General Public License v3.0. Zobacz [LICENSE](../LICENSE).
