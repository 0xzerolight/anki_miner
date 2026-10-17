<!-- i18n-source: README.md sha256:7dd1b505cab496be -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="Версія на PyPI"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="Ліцензія: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="Завантаження з GitHub"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="Зірки на GitHub"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Спільнота в Discord"></a>
</p>

<!-- i18n-nav:start -->
<div align="center">
<details>
<summary>Мова: Українська</summary>
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
<b>Українська</b> ·
<a href="README.hr.md">Hrvatski</a> ·
<a href="README.lt.md">Lietuvių</a> ·
<a href="README.fi.md">Suomi</a> ·
<a href="README.hu.md">Magyar</a> ·
<a href="README.el.md">Ελληνικά</a> ·
<a href="README.ro.md">Română</a> ·
<a href="README.tr.md">Türkçe</a>
</p>
</details>
</div>
<!-- i18n-nav:end -->

<p align="center">
Перетворюйте оригінальний контент японською, китайською, корейською та ще двадцятьма дев’ятьма мовами на лексичні картки Anki.
</p>

<p align="center">
Також є для Android — <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner для Android</a>.
</p>

<p align="center">
Якщо Anki Miner вам допоміг, поставте ⭐ зірку — так його легше знайти іншим :).
</p>


# <p align="center">Демонстрація видобування</p>

![Демонстрація Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Повна демонстрація зі звуком (MP4)</a></p>

### Приклади карток

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (зі звуком)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (зі звуком)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (зі звуком)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Встановлення

### Вимоги

- **Anki** з доповненням [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (код `2055492159`)
- **ffmpeg** + **libmpv** (лише для попереднього перегляду відео) — потрібні тільки під час встановлення через pip/pipx або з вихідного коду.

Завантажте файл для своєї платформи з [останнього випуску](https://github.com/0xzerolight/anki_miner/releases/latest):

| Платформа | Завантаження |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (інші) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Без знімків екрана AVIF і вилучення тиші для створених субтитрів.

### Примітки до першого запуску (непідписані збірки)

- **macOS**: відкрийте `.dmg`, перетягніть **AnkiMiner** до **Програми**, потім запустіть. Перший запуск блокується, бо застосунок не нотаризовано: закрийте діалог, знову відкрийте застосунок і виберіть **Усе одно відкрити** (також у розділі **Системні параметри** -> **Приватність і безпека**). Лише один раз.
- **Linux AppImage**: перед запуском зробіть файл виконуваним — `chmod +x AnkiMiner-*.AppImage` або **Властивості** -> **Дозволити виконання** у файловому менеджері.
- **Windows SmartScreen**: **Докладніше** -> **Усе одно запустити**.
- **Хибне спрацювання Windows Defender**: відновіть файл з **Журналу захисту** або [повідомте Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Встановлення з PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

Японській, індонезійській та івриту нічого додаткового не потрібно. Для іншої мови видобування додайте її рушій:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

Для арабської та перської окремого пакета немає. Їхні дані, а також моделі spaCy для європейських мов, завантажуються в застосунку в розділі Налаштування -> Мова видобування; готові збірки вище завантажують усе там само.

</details>

<details>
<summary><strong>Встановлення з вихідного коду</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Повне налаштування середовища розробки описано в [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Вкладки

- **Відео** — видобування з однієї пари відео/субтитри, теки для пакетної обробки, URL-адрес YouTube чи Bilibili або цілого серіалу в одну колоду, упорядковану за частотністю.
- **Аудіокниги** — видобування з аудіокниг, подкастів, радіо, пісень (пари аудіо + субтитри/транскрипт).
- **Читання** — видобування з манґи (mokuro), романів (`.epub`, `.txt`; одна книжка або ціла тека), окремих файлів субтитрів або вставленого тексту.
- **Аналітика** — історія видобування, рейтинги складності, досягнення.
- **Утиліти** — створення субтитрів (локальний Whisper), виправлення таймінгу субтитрів (ffsubsync/alass), стиснення медіа до аудіо лише з діалогами, завантаження відео/аудіо/субтитрів з будь-якого сайту, який підтримує yt-dlp, копіювання вартої вивчення частини готової колоди в нову, доповнення полів наявних карток, OCR зображень сторінок манґи у файли .mokuro (mokuro) і синхронізація аудіокниги з текстом книжки («Синхр. книг»).
- **Налаштування** — усе, що можна налаштувати.

## Інші можливості

- Мови видобування — японська, китайська, корейська, англійська, каталонська, німецька, португальська, французька, іспанська, італійська, нідерландська, норвезька (букмол), румунська, грецька, фінська, угорська, хорватська, шведська, польська, литовська, данська, турецька, індонезійська, російська, арабська, тайська, перська, словенська, українська, в’єтнамська, кантонська та іврит; перемикаються в налаштуваннях. Мови, крім японської, завантажують свій рушій у застосунку; індонезійській та івриту він не потрібен.
- Куратор слів — перегляд кожного слова-кандидата перед створенням карток, поруч зі сценою, сторінкою манґи та словниковою статтею.
- Відміна запуску — видалення нотаток, щойно створених запуском, просто з вікна його результатів.
- Широке фільтрування: i+1, діапазон рангів частотності, чорний список, регулярні вирази, набори слів тощо.
- Імпорт офлайн-словників Yomitan — визначення, тональний наголос, частотність — у ланцюжку за пріоритетом.
- Кілька списків частотності в ланцюжку за пріоритетом.
- Аудіо слів на картках з локальних аудіопакетів, JapanesePod101, Google TTS або Microsoft Edge TTS.
- Аудіо речень на картках із «Читання» з Google Translate TTS або Naver Papago для японської та корейської (типово вимкнено).
- Стилізація глосаріїв для кожного словника окремо, як у Yomitan.
- Вбудований попередній перегляд відео на libmpv — відтворення сцени слова під час вибору або підлаштування таймінгу субтитрів із живим відтворенням.
- Анімовані знімки екрана (див. приклади карток вище).
- Профілі налаштувань — збереження іменованих конфігурацій і перемикання між ними із заголовка.
- Доповнення — заповнення відсутніх визначень, частотності, тонального наголосу, читань і аудіо слів на вже створених картках або оновлення їхнього стилю (Утиліти → Доповнення).

<details>
<summary><strong>Вбудовані теми (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (світла); Frappé, Macchiato, Mocha (темні)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (світла), Wave (темна)
- **Rosé Pine** - Dawn (світла); Main, Moon (темні)
- **Solarized** - Light, Dark
- **Окремі** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Ліцензії тем: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Хочете іншу тему? Запропонуйте її в GitHub Issue.

</details>

<details>
<summary><strong>Як це працює</strong></summary>

1. **Читає субтитри** й розбиває текст на окремі слова.
2. **Відфільтровує** повнозначні слова, яких ви ще не знаєте, — за бажанням ви можете самі переглянути список у «Кураторі слів».
3. **Робить знімок екрана й аудіокліп** з відео для кожного рядка.
4. **Шукає визначення** в налаштованих офлайн-словниках.
5. **Надсилає готові картки в Anki.**

</details>

## Рекомендовані ресурси

Словники й списки частотності для кожної мови видобування з посиланнями на завантаження: [RESOURCES.md](../RESOURCES.md). Майстер налаштування пропонує відповідний набір для вашої мови видобування.

<details>
<summary><strong>Ліцензія JMnedict</strong></summary>

Використовує вбудовані набори імен, створені на основі [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (проєкт JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Усунення проблем

| Проблема                    | Рішення                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| «Не вдалося з’єднатися з Anki» | Запустіть Anki й переконайтеся, що встановлено AnkiConnect.                                  |
| «Колоду не знайдено»         | Виберіть наявну колоду в розділі Налаштування -> Картки й Anki. Колоди не створюються автоматично; якщо потрібна нова, спершу створіть її в Anki. |
| «Тип нотатки не знайдено»    | Налаштуйте назви полів свого типу нотатки в розділі Налаштування -> Картки й Anki.               |
| «ffmpeg не знайдено»       | Установіть ffmpeg і додайте його до PATH.                                               |
| Не знайдено визначень     | Додайте словник Yomitan: Налаштування -> Словники -> Додати словник… |
| Інсталятор Windows не відкривається / попередження SmartScreen | Див. [Примітки до першого запуску](#примітки-до-першого-запуску-непідписані-збірки): виберіть **Докладніше** -> **Усе одно запустити**; хибні спрацювання Defender відновлюйте з **Журналу захисту**. |
| Після нового встановлення немає визначень | Запустіть Інструменти -> Майстер налаштування або Інструменти -> Завантажити рекомендовані ресурси. Для ручного імпорту залишайте ZIP-файл Yomitan як є (не розпаковуйте його). |
| Додавання словника зависає або завершується помилкою | Запам’ятайте останній видимий етап і додайте журнали (див. «Де журнали?» нижче). Вкажіть у звіті назву ZIP-файлу словника, його джерело й розмір. |
| Де журнали?      | Скористайтеся пунктом Довідка -> Відкрити теку журналів або відкрийте `%USERPROFILE%\.anki_miner\anki_miner.log` у Windows чи `~/.anki_miner/anki_miner.log` у macOS/Linux. Ротовані журнали мають суфікси від `.1` до `.5`. Надішліть також `anki_miner.crash`, якщо він є (збій, що закрив застосунок, записує свій стек у цей файл, а не в журнал), і `anki_miner.child.log`, що містить вивід допоміжного процесу. |
| Повідомлення про помилку          | Довідка -> Експортувати діагностику… записує ZIP-файл у вибране вами місце; він містить журнали (`anki_miner.log` та його ротації, `anki_miner.crash`, `anki_miner.child.log`), ваш `settings.json`, файли конфігурації та стану інтерфейсу, знімки черги й маніфести завантажень, а також згенеровані звіти про стан комп’ютера й застосунку (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Перегляньте його перед вивантаженням, бо він містить шляхи й назви файлів з вашого комп’ютера. Нічого не вивантажується автоматично. |
| Докладніше діагностичне журналювання | Задайте `ANKI_MINER_LOG_LEVEL=DEBUG` перед запуском Anki Miner, щоб записувати подробиці сторонніх yt-dlp, urllib3 і fugashi. Типове значення — `WARNING`; журнали самого Anki Miner залишаються на рівні DEBUG. |
| Аудіо не тією мовою  | Інструмент вибирає аудіодоріжку мовою видобування, а якщо її немає — першу. Виберіть її самі за допомогою «Аудіодоріжка…» (Відео -> Одне). |
| Субтитри не синхронізовано    | Скористайтеся регулятором зсуву субтитрів в інтерфейсі (діапазон ±300 секунд).                 |

## Участь у розробці

Будь-який внесок вітається.
Якщо хочете підтримати проєкт, розкажіть про нього тим, кому він може стати в пригоді.

- Уперше тут? Почніть із [CONTRIBUTING.md](../CONTRIBUTING.md).
- Огляд архітектури: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Кодекс поведінки: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Безпека: [SECURITY.md](../SECURITY.md).

Звіти про помилки й пропозиції функцій -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Загальні запитання й обговорення -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) або [Discord](https://discord.com/invite/aDtQyZzUVP).

## Особлива подяка

Щира подяка людям, які зробили винятковий внесок у проєкт:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** — блискучі пропозиції функцій, тестування нових випусків, розбудова спільноти.
- ★ **[rob-olvr](https://github.com/rob-olvr)** — чудові пропозиції функцій, розбудова спільноти й модерація в Discord.

Усіх, хто зробив будь-який внесок у проєкт, наведено в [CONTRIBUTORS.md](../CONTRIBUTORS.md).


## Ліцензія

GNU General Public License v3.0. Див. [LICENSE](../LICENSE).
