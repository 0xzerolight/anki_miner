<!-- i18n-source: README.md sha256:06b01f0d7cb8ab71 -->

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
Превращайте нативный контент на японском, китайском, корейском и ещё двадцати девяти языках в карточки Anki для изучения слов.
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
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (прочие) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Без скриншотов AVIF и без удаления тишины в сгенерированных субтитрах.

### Заметки о первом запуске (неподписанные сборки)

- **macOS**: откройте `.dmg`, перетащите **AnkiMiner** в **Программы** и запустите. Первый запуск блокируется, потому что приложение не заверено у Apple: закройте диалог, откройте приложение ещё раз и выберите **Всё равно открыть** (также в **Системных настройках** -> **Конфиденциальность и безопасность**). Только один раз.
- **Linux AppImage**: перед запуском сделайте файл исполняемым - `chmod +x AnkiMiner-*.AppImage` или **Свойства** -> **Разрешить выполнение** в файловом менеджере.
- **Windows SmartScreen**: **Подробнее** -> **Выполнить в любом случае**.
- **Ложное срабатывание Windows Defender**: восстановите файл из **Журнала защиты** или [сообщите об этом в Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Установка из PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Для японского, индонезийского и иврита ничего дополнительно не нужно. Для майнинга другого языка добавьте его движок:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

У арабского и персидского нет extra. Их данные и модели spaCy для европейских языков скачиваются в приложении: Настройки -> Язык майнинга; сборки выше получают там всё.

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

- **Видео** - майнинг одной пары видео/субтитры, пакетной папки, ссылок YouTube или целого сериала в одну колоду, отсортированную по частотности.
- **Аудиокниги** - майнинг аудиокниг, подкастов, радио, песен (пары аудио + субтитры/транскрипт).
- **Чтение** - майнинг манги (mokuro), новелл (`.epub`, `.txt`; одна книга или целая папка), отдельных файлов субтитров или вставленного текста.
- **Аналитика** - история майнинга, рейтинги сложности, достижения.
- **Утилиты** - генерация субтитров (локальный Whisper), синхронизация субтитров по времени (ffsubsync/alass), сжатие медиа до аудио только с диалогами, загрузка видео/аудио/субтитров с любого сайта, поддерживаемого yt-dlp, копирование достойной изучения части готовой колоды в новую, дозаполнение полей на существующих карточках, OCR изображений страниц манги в файлы .mokuro (mokuro), а также синхронизация аудиокниги с текстом её книги (Синхронизация аудиокниги).
- **Настройки** - все параметры конфигурации.

## Другие возможности

- Языки майнинга - японский, китайский, корейский, английский, каталанский, немецкий, португальский, французский, испанский, итальянский, нидерландский, норвежский (букмол), румынский, греческий, финский, венгерский, хорватский, шведский, польский, литовский, датский, турецкий, индонезийский, русский, арабский, тайский, персидский, словенский, украинский, вьетнамский, кантонский и иврит, переключаются в Настройках. Все языки, кроме японского, скачивают свой движок прямо в приложении; индонезийскому и ивриту он не нужен.
- Куратор слов - просмотр каждого слова-кандидата перед созданием карточек, со сценой, страницей манги и словарной статьёй рядом.
- Отмена запуска - удалите заметки, только что созданные запуском, прямо из диалога результатов.
- Обширная фильтрация: i+1, диапазон рангов частотности, чёрный список, regex, наборы слов и другое.
- Импорт офлайн-словарей Yomitan - определения, тональное ударение, частотность - объединяются по приоритету.
- Несколько списков частотности, объединяемых по приоритету.
- Аудио слов на карточках из локальных аудиопакетов, JapanesePod101, Google TTS или Microsoft Edge TTS.
- Аудио предложений на карточках Чтения из Google Translate TTS или, для японского и корейского, Naver Papago (по умолчанию выключено).
- Оформление глоссария для каждого словаря отдельно, в стиле Yomitan.
- Встроенный предпросмотр видео на libmpv - воспроизведение сцены слова во время курирования или подстройка времени субтитров с живым воспроизведением.
- Анимированные скриншоты (см. примеры карточек выше).
- Профили настроек - сохраняйте именованные конфигурации и переключайтесь между ними из шапки.
- Изменить стиль намайненных карточек - повторное применение текущего оформления карточек к уже созданным карточкам (Утилиты → Дозаполнение карточек).

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

1. **Прочитать субтитры** и разбить текст на отдельные слова.
2. **Отфильтровать** до значимых слов, которые вы ещё не знаете - при желании можно самостоятельно просмотреть список в Кураторе слов.
3. **Захватить скриншот и аудиофрагмент** из видео для каждой строки.
4. **Найти определения** в настроенных офлайн-словарях, для японского при необходимости с резервным обращением к Jisho онлайн (медленнее, с ограничением частоты запросов).
5. **Отправить готовые карточки в Anki.**

</details>

## Рекомендуемые ресурсы

Словари и частотные списки для каждого языка майнинга со ссылками на скачивание: [RESOURCES.md](../RESOURCES.md). Мастер настройки предложит подходящий набор для вашего языка майнинга.

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
| Определения не найдены     | Добавьте словарь Yomitan в Настройки -> Словари -> Добавить словарь… (рекомендуется) или, для японского, включите резервный вариант с Jisho (медленнее, с ограничением частоты запросов). |
| Установщик Windows не открывается / предупреждение SmartScreen | См. [Заметки о первом запуске](#заметки-о-первом-запуске-неподписанные-сборки): выберите **Подробнее** -> **Выполнить в любом случае**; восстановите ложные срабатывания Defender из **Журнала защиты**. |
| После чистой установки нет определений | Запустите Инструменты -> Мастер настройки или Инструменты -> Загрузить рекомендуемые ресурсы. Для ручного импорта не распаковывайте ZIP-архив Yomitan. |
| Добавление словаря зависает или завершается ошибкой | Отметьте последний видимый этап и приложите журналы (см. «Где найти журналы?» ниже). Укажите в отчёте имя ZIP-архива словаря, источник и размер. |
| Где найти журналы?      | Используйте Справка -> Открыть папку журналов или откройте `%USERPROFILE%\.anki_miner\anki_miner.log` на Windows либо `~/.anki_miner/anki_miner.log` на macOS/Linux. У ротированных журналов суффиксы от `.1` до `.5`. Приложите также `anki_miner.crash`, если он есть: аварийное завершение записывает стек именно в этот файл, а не в журнал, - и `anki_miner.child.log` с выводом вспомогательного процесса. |
| Как сообщить об ошибке          | Справка -> Экспорт диагностики… записывает в выбранное вами место ZIP-архив с журналами (`anki_miner.log` и его ротации, `anki_miner.crash`, `anki_miner.child.log`), вашим `settings.json`, файлами конфигурации и состояния интерфейса, снимками очередей и манифестами загрузок, а также со сформированными отчётами о машине и состоянии приложения (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Проверьте его перед отправкой, так как он содержит пути и имена файлов с вашего компьютера. Автоматически ничего не отправляется. |
| Более подробное журналирование | Установите `ANKI_MINER_LOG_LEVEL=DEBUG` перед запуском Anki Miner, чтобы фиксировать подробности сторонних yt-dlp, urllib3 и fugashi. По умолчанию `WARNING`; журналы Anki Miner остаются на уровне DEBUG. |
| Аудио не на том языке  | Инструмент выбирает аудиодорожку на языке майнинга, а если её нет, первую. Выберите её сами кнопкой Дорожки (Видео -> Один).      |
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
  - [x] Портирование на Android - https://github.com/0xzerolight/anki_miner_android
  - [x] За пределами японского: ещё тридцать один язык майнинга.
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
