<!-- i18n-source: README.md sha256:1f22dd6c4029097f -->

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
<b>Русский</b> ·
<a href="README.fr.md">Français</a> ·
<a href="README.es.md">Español</a> ·
<a href="README.de.md">Deutsch</a> ·
<a href="README.pt_br.md">Português (Brasil)</a> ·
<a href="README.id.md">Bahasa Indonesia</a> ·
<a href="README.vi.md">Tiếng Việt</a> ·
<a href="README.zh_cn.md">简体中文</a> ·
<a href="README.zh_tw.md">繁體中文</a> ·
<a href="README.it.md">Italiano</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Превращайте нативный японский контент в карточки Anki для изучения слов.
</p>

<p align="center">
Также на Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner для Android</a>.
</p>

<p align="center">
Поставьте ⭐ звезду, если Anki Miner вам помог - это помогает другим найти проект :).
</p>


# <p align="center">Демонстрация майнинга</p>

![Anki Miner Showcase](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Полная демонстрация со звуком (MP4)</a></p>

### Примеры карточек

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (sound)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Установка

### Требования

- **Anki** с дополнением [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (код `2055492159`)
- **ffmpeg** + **libmpv** (только для предпросмотра видео) - нужны только при установке через pip/pipx или из исходников.

Скачайте версию для вашей платформы из [последнего релиза](https://github.com/0xzerolight/anki_miner/releases/latest):

| Платформа | Файл |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon / M1-M4) | `AnkiMiner-macOS-arm64.tar.gz` |
| macOS (Intel) | `AnkiMiner-macOS-x86_64.tar.gz` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (прочие) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Не включает локальную генерацию субтитров через Whisper и AVIF-скриншоты. Для полной функциональности: `pipx install "anki-miner[asr]"`.

### Заметки о первом запуске (неподписанные сборки)

- **macOS**: Gatekeeper блокирует приложение. Сначала распакуйте, затем выполните `xattr -dr com.apple.quarantine AnkiMiner/`
- **Windows SmartScreen**: **Подробнее** -> **Выполнить в любом случае**.
- **Ложное срабатывание Windows Defender**: восстановите файл из **Журнала защиты** или [сообщите об этом в Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Установка из PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

</details>

<details>
<summary><strong>Установка из исходников</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Полную инструкцию по настройке окружения разработки см. в [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Вкладки

- **Видео** - майнинг одной пары видео/субтитры, пакетной папки или ссылок YouTube.
- **Конструктор колод** - майнинг целого сериала в одну колоду, отсортированную по частотности.
- **Аудиокниги** - майнинг аудиокниг, подкастов, радио, песен (пары аудио + субтитры/транскрипт).
- **Чтение** - майнинг манги (mokuro), новелл (`.epub`, `.txt`; одна книга или целая папка), отдельных файлов субтитров или вставленного японского текста.
- **Аналитика** - история майнинга, рейтинги сложности, достижения.
- **Утилиты** - генерация субтитров (локальный Whisper), синхронизация субтитров по времени (alass), сжатие медиа до аудио только с диалогами, копирование достойной изучения части готовой колоды в новую, а также дозаполнение полей на существующих карточках.
- **Настройки** - все параметры конфигурации.

## Другие возможности

- Куратор слов - просмотр каждого слова-кандидата перед созданием карточек, со сценой, страницей манги и словарной статьёй рядом.
- Отмена запуска - удалите заметки, только что созданные запуском, прямо из диалога результатов.
- Обширная фильтрация: i+1, диапазон рангов частотности, чёрный список, regex, наборы слов и другое.
- Импорт офлайн-словарей Yomitan - определения, тональное ударение, частотность - объединяются по приоритету.
- Несколько списков частотности, объединяемых по приоритету.
- Аудио слов на карточках из локальных аудиопакетов, JapanesePod101 или Google TTS.
- Аудио предложений на карточках Чтения из Google Translate TTS или Naver Papago (по умолчанию выключено).
- Оформление глоссария для каждого словаря отдельно, в стиле Yomitan.
- Встроенный предпросмотр видео на libmpv - воспроизведение сцены слова во время курирования или подстройка времени субтитров с живым воспроизведением.
- Анимированные скриншоты (см. примеры карточек выше).
- Профили настроек - сохраняйте именованные конфигурации и переключайтесь между ними из шапки.
- Изменить стиль намайненных карточек - повторное применение текущего оформления карточек к уже созданным карточкам (меню Инструменты).

<details>
<summary><strong>Встроенные темы (29)</strong></summary>

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

Лицензии тем: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Хотите, чтобы добавили ещё тему? Предложите её в GitHub Issue.

</details>

<details>
<summary><strong>Как это работает</strong></summary>

1. **Прочитать субтитры** и разбить японский текст на отдельные слова.
2. **Отфильтровать** до значимых слов, которые вы ещё не знаете - при желании можно самостоятельно просмотреть список в Кураторе слов.
3. **Захватить скриншот и аудиофрагмент** из видео для каждой строки.
4. **Найти определения** в настроенных офлайн-словарях, при необходимости с резервным обращением к Jisho онлайн (медленнее, с ограничением частоты запросов).
5. **Отправить готовые карточки в Anki.**

</details>

## Рекомендуемые ресурсы

| Тип | Ресурс | Загрузка | Как добавить |
|------|----------|----------|---------|
| Словарь | [JMdict](https://github.com/yomidevs/jmdict-yomitan) | [Yomitan zip](https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip) | Добавить словарь… |
| Словарь | [Jitendex](https://jitendex.org/) | [Yomitan zip](https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip) | Добавить словарь… |
| Словарь | [Bee's Character Dictionary](https://characterdictionary.tokyo/) | Формируется на сайте | Добавить словарь… |
| Тональное ударение | [Kanjium](https://github.com/mifunetoshiro/kanjium) | [TSV](https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt) | Тональное ударение -> Добавить источник тонального ударения… |
| Тональное ударение | [アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries) | [Drive](https://drive.google.com/drive/folders/1tTdLppnqMfVC5otPlX_cs4ixlIgjv_lH) | Тональное ударение -> Добавить источник тонального ударения… |
| Частотность | [JPDB v2.2 Kana](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13.zip) | Частотность -> Добавить источник частотности… |
| Частотность | [BCCWJ SUW+LUW](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/BCCWJ_SUW_LUW_combined.zip) | Частотность -> Добавить источник частотности… |


<details>
<summary><strong>Лицензия JMnedict</strong></summary>

Использует встроенные наборы имён, полученные из [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (проект JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Устранение неполадок

| Проблема                    | Решение                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| «Не удаётся подключиться к Anki» | Запустите Anki и убедитесь, что AnkiConnect установлен.                                  |
| «Колода не найдена»         | Выберите существующую колоду в Настройки -> Карточки и Anki. Колоды не создаются автоматически; сначала создайте её в Anki, если нужна новая. |
| «Тип заметки не найден»    | Настройте названия полей типа заметки в Настройки -> Карточки и Anki.               |
| «ffmpeg не найден»       | Установите ffmpeg и добавьте его в PATH.                                               |
| Определения не найдены     | Добавьте словарь Yomitan в Настройки -> Добавить словарь… (рекомендуется) или включите резервный вариант с Jisho (медленнее, с ограничением частоты запросов). |
| Установщик Windows не открывается / предупреждение SmartScreen | См. [Заметки о первом запуске](#заметки-о-первом-запуске-неподписанные-сборки): выберите **Подробнее** -> **Выполнить в любом случае**; восстановите ложные срабатывания Defender из **Журнала защиты**. |
| После чистой установки нет определений | Запустите Инструменты -> Мастер настройки или Инструменты -> Загрузить рекомендуемые ресурсы. Для ручного импорта не распаковывайте ZIP-архив Yomitan. |
| Добавление словаря зависает или завершается ошибкой | Отметьте последний видимый этап и приложите журналы (см. «Где найти журналы?» ниже). Укажите в отчёте имя ZIP-архива словаря, источник и размер. |
| Где найти журналы?      | Используйте Справка -> Открыть папку журналов или откройте `%USERPROFILE%\.anki_miner\anki_miner.log` на Windows либо `~/.anki_miner/anki_miner.log` на macOS/Linux. У ротированных журналов суффиксы от `.1` до `.5`. |
| Как сообщить об ошибке          | Справка -> Экспорт диагностики… записывает ZIP-архив с журналами и данными о системе в выбранное вами место. Проверьте его перед отправкой, так как он содержит пути и имена файлов с вашего компьютера. Автоматически ничего не отправляется. |
| Более подробное журналирование | Установите `ANKI_MINER_LOG_LEVEL=DEBUG` перед запуском Anki Miner, чтобы фиксировать подробности сторонних yt-dlp, urllib3 и fugashi. По умолчанию `WARNING`; журналы Anki Miner остаются на уровне DEBUG. |
| Аудио не на том языке  | Инструмент сначала пробует японские аудиодорожки, затем переключается на дорожку по умолчанию.      |
| Субтитры рассинхронизированы    | Используйте регулятор смещения субтитров в интерфейсе (диапазон ±300 секунд).                 |

## Планы на будущее

Список идей для будущих версий Anki Miner. Без порядка приоритета. Запросы функций имеют приоритет.
- Предложить функцию - [Откройте issue](https://github.com/0xzerolight/anki_miner/issues).
- Обсудить планы - [Discussions](https://github.com/0xzerolight/anki_miner/discussions).

- **Функции**:
  - [x] Выбор языка интерфейса.
  - [x] Вкладка локального создания субтитров: опциональная вкладка для генерации субтитров на месте.
  - [x] Вкладка Чтение: майнинг манги и книг.
  - [x] Инструмент дозаполнения.
  - [ ] Медиатека: расширить вкладку Аналитика для отображения локальной медиатеки по всем видам медиа.
  - [ ] Автоматическая загрузка субтитров.

- **Долгосрочные планы**:
  - [x] Портирование на Android -- https://github.com/0xzerolight/anki_miner_android
  - [ ] За пределами японского: майнинг других языков.
  - [ ] Расширение для браузера Anki Miner.


## Участие в разработке

Приветствуется любой вклад в проект.
Если хотите поддержать проект, поделитесь им с теми, кому он может пригодиться.

- Впервые здесь? Начните с [CONTRIBUTING.md](../CONTRIBUTING.md).
- Обзор архитектуры: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Кодекс поведения: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Безопасность: [SECURITY.md](../SECURITY.md).

Сообщения об ошибках и запросы функций -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Общие вопросы и обсуждения -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) или [Discord](https://discord.com/invite/aDtQyZzUVP).

## Особая благодарность

Искренняя благодарность людям, внёсшим исключительный вклад в проект:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - блестящие предложения по функциям, тестирование новых релизов, развитие сообщества.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - отличные предложения по функциям, развитие сообщества и модерация в Discord.

Список всех, кто внёс любой вклад в проект, см. в [CONTRIBUTORS.md](../CONTRIBUTORS.md).


## Лицензия

GNU General Public License v3.0. См. [LICENSE](../LICENSE).
