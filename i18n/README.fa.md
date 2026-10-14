<!-- i18n-source: README.md sha256:e349c147c5d08d7d -->

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
<b>فارسی</b> ·
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
<a href="README.ro.md">Română</a>
</p>
<!-- i18n-nav:end -->

<div dir="rtl">

<p align="center">
محتوای بومی به زبان‌های ژاپنی، چینی، کره‌ای و بیست‌ونه زبان دیگر را به کارت‌های واژگان Anki تبدیل کنید.
</p>

<p align="center">
برای Android هم موجود است - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner برای Android</a>.
</p>

<p align="center">
اگر Anki Miner به شما کمک کرد، لطفاً یک ⭐ ستاره بدهید - این کار به دیگران کمک می‌کند آن را پیدا کنند :).
</p>


# <p align="center">نمایش استخراج</p>

![نمایش Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">نمایش کامل با صدا (MP4)</a></p>

### نمونه کارت‌ها

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (با صدا)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (با صدا)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (با صدا)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## نصب

### پیش‌نیازها

- **Anki** همراه با افزونه [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (کد `2055492159`)
- **ffmpeg** + **libmpv** (فقط برای پیش‌نمایش ویدیو) - فقط هنگام نصب از طریق pip/pipx یا کد منبع لازم است.

فایل مناسب سکوی خود را از [آخرین انتشار](https://github.com/0xzerolight/anki_miner/releases/latest) دانلود کنید:

| سکو | دانلود |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (سایر توزیع‌ها) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ بدون تصویر صفحه AVIF و بدون حذف سکوت برای زیرنویس‌های تولیدشده.

### نکته‌های اجرای نخست (نسخه‌های امضانشده)

- **macOS**: فایل `.dmg` را باز کنید، **AnkiMiner** را روی **Applications** بکشید و سپس اجرا کنید. اجرای نخست مسدود می‌شود، چون برنامه notarize نشده است: پنجره را ببندید، برنامه را دوباره باز کنید و **Open Anyway** را انتخاب کنید (همچنین در **System Settings** -> **Privacy & Security**). فقط یک بار.
- **Linux AppImage**: پیش از اجرا آن را اجرایی کنید - `chmod +x AnkiMiner-*.AppImage`، یا در مدیر فایل خود **Properties** -> **Allow executing**.
- **Windows SmartScreen**: **More info** -> **Run anyway**.
- **هشدار نادرست Windows Defender**: از **Protection history** بازیابی کنید یا [به Microsoft گزارش دهید](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>نصب از PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

ژاپنی، اندونزیایی و عبری به چیز اضافه‌ای نیاز ندارند. برای زبان استخراج دیگر، موتور آن را اضافه کنید:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

عربی و فارسی بسته اضافه ندارند. داده‌های آن‌ها، و مدل‌های spaCy زبان‌های اروپایی، درون برنامه از تنظیمات ← زبان استخراج دانلود می‌شوند؛ در فایل‌های دانلودی بالا همه چیز از همان‌جا واکشی می‌شود.

</details>

<details>
<summary><strong>نصب از کد منبع</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

برای راه‌اندازی کامل محیط توسعه، [CONTRIBUTING.md](../CONTRIBUTING.md) را ببینید.

</details>

## زبانه‌ها

- **ویدیو** - استخراج از یک جفت ویدیو/زیرنویس، یک پوشه گروهی، URLهای YouTube یا Bilibili، یا یک مجموعه کامل در یک دسته مرتب‌شده بر اساس بسامد.
- **کتاب صوتی** - استخراج از کتاب‌های صوتی، پادکست‌ها، رادیو و آهنگ‌ها (جفت‌های صوت + زیرنویس/متن پیاده‌شده).
- **خواندن** - استخراج از مانگا (mokuro)، رمان‌ها (`.epub`، `.txt`؛ یک کتاب یا یک پوشه کامل)، فایل‌های زیرنویس مستقل یا متن چسبانده‌شده.
- **آمار** - تاریخچه استخراج، رتبه‌بندی دشواری، نقاط عطف.
- **امکانات** - تولید زیرنویس (Whisper محلی)، زمان‌بندی دوباره زیرنویس (ffsubsync/alass)، فشرده‌سازی رسانه به صوتِ فقط گفت‌وگو، دانلود ویدیو/صوت/زیرنویس از هر سایتی که yt-dlp پشتیبانی می‌کند، کپی بخش ارزشمند یک دسته آماده در یک دسته جدید، تکمیل فیلدهای کارت‌های موجود، OCR تصاویر صفحه‌های مانگا به فایل‌های .mokuro (mokuro)، و هم‌زمان‌سازی یک کتاب صوتی با متن کتابش (همگام‌سازی کتاب).
- **تنظیمات** - همه چیزهای قابل پیکربندی.

## ویژگی‌های دیگر

- زبان‌های استخراج - ژاپنی، چینی، کره‌ای، انگلیسی، کاتالان، آلمانی، پرتغالی، فرانسوی، اسپانیایی، ایتالیایی، هلندی، نروژی بوکمول، رومانیایی، یونانی، فنلاندی، مجاری، کرواتی، سوئدی، لهستانی، لیتوانیایی، دانمارکی، ترکی، اندونزیایی، روسی، عربی، تایلندی، فارسی، اسلوونیایی، اوکراینی، ویتنامی، کانتونی و عبری، که در تنظیمات تعویض می‌شوند. زبان‌های غیر از ژاپنی موتور خود را درون برنامه دانلود می‌کنند؛ اندونزیایی و عبری به موتوری نیاز ندارند.
- گزینشگر واژه - هر واژه نامزد را پیش از ساخت کارت‌ها، همراه با صحنه، صفحه مانگا و مدخل واژه‌نامه آن در کنار هم، بازبینی کنید.
- واگرد یک اجرا - یادداشت‌هایی را که یک اجرا همین حالا ساخته، مستقیماً از پنجره نتایج آن حذف کنید.
- فیلترهای گسترده: i+1، بازه رتبه بسامد، فهرست سیاه، عبارت باقاعده، مجموعه‌های واژه و موارد دیگر.
- وارد کردن آفلاین واژه‌نامه‌های Yomitan - تعریف‌ها، تکیه زیروبمی، بسامد - به‌صورت زنجیره‌ای بر اساس اولویت.
- چند فهرست بسامد به‌صورت زنجیره‌ای بر اساس اولویت.
- صوت واژه روی کارت‌ها از بسته‌های صوتی محلی، JapanesePod101، Google TTS یا Microsoft Edge TTS.
- صوت جمله روی کارت‌های خواندن از Google Translate TTS، یا Naver Papago برای ژاپنی و کره‌ای (به‌طور پیش‌فرض خاموش).
- سبک‌دهی واژه‌نما برای هر واژه‌نامه، به سبک Yomitan.
- پیش‌نمایش ویدیوی جاسازی‌شده libmpv - هنگام گزینش، صحنه یک واژه را پخش کنید یا زمان‌بندی زیرنویس را با پخش زنده تنظیم کنید.
- تصویر صفحه متحرک (نمونه کارت‌های بالا را ببینید).
- پروفایل‌های تنظیمات - پیکربندی‌های نام‌دار را ذخیره کنید و از سرصفحه میان آن‌ها جابه‌جا شوید.
- تغییر سبک کارت‌های استخراج‌شده - سبک فعلی کارت را دوباره روی کارت‌هایی که قبلاً ساخته‌اید اعمال کنید (امکانات ← تکمیل کارت).

<details>
<summary><strong>پوسته‌های داخلی (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (روشن)؛ Frappé، Macchiato، Mocha (تیره)
- **Dracula** - Dracula، Alucard
- **Everforest** - Light، Dark
- **GitHub** - Light؛ Dark، Dark Dimmed
- **Gruvbox** - Light Medium، Dark Medium
- **Kanagawa** - Lotus (روشن)، Wave (تیره)
- **Rosé Pine** - Dawn (روشن)؛ Main، Moon (تیره)
- **Solarized** - Light، Dark
- **Standalone** - Light، Dark، Sakura، Nord، One Dark، Tokyo Night

مجوزهای پوسته‌ها: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
پوسته دیگری می‌خواهید؟ آن را در یک GitHub Issue پیشنهاد دهید.

</details>

<details>
<summary><strong>چگونه کار می‌کند</strong></summary>

1. **زیرنویس‌ها را می‌خواند** و متن را به واژه‌های جداگانه تقسیم می‌کند.
2. **فیلتر می‌کند** تا فقط واژه‌های محتوایی که هنوز نمی‌دانید بمانند - و در صورت تمایل، خودتان فهرست را در «گزینشگر واژه» بازبینی می‌کنید.
3. **برای هر سطر یک تصویر صفحه و کلیپ صوتی** از ویدیو برمی‌دارد.
4. **تعریف‌ها را** در واژه‌نامه‌های آفلاین پیکربندی‌شده شما **جستجو می‌کند**.
5. **کارت‌های آماده را به Anki می‌فرستد.**

</details>

## منابع پیشنهادی

واژه‌نامه‌ها و فهرست‌های بسامد برای هر زبان استخراج، همراه با پیوندهای دانلود: [RESOURCES.md](../RESOURCES.md). «راهنمای راه‌اندازی» مجموعه مناسب زبان استخراج شما را پیشنهاد می‌کند.

<details>
<summary><strong>مجوز JMnedict</strong></summary>

از مجموعه‌های واژه نام همراه برنامه استفاده می‌کند که از [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (پروژه JMdict/EDICT، EDRDG، CC BY-SA 4.0) گرفته شده‌اند.

</details>

## عیب‌یابی

| مشکل                    | راه‌حل                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| «اتصال به Anki ممکن نیست» | Anki را اجرا کنید و مطمئن شوید AnkiConnect نصب است.                                  |
| «دسته یافت نشد»         | در تنظیمات ← کارت‌ها و Anki یک دسته موجود انتخاب کنید. دسته‌ها برای شما ساخته نمی‌شوند؛ اگر دسته جدیدی لازم دارید، ابتدا آن را در Anki بسازید. |
| «نوع یادداشت یافت نشد»    | نام فیلدهای نوع یادداشت خود را در تنظیمات ← کارت‌ها و Anki پیکربندی کنید.               |
| «ffmpeg یافت نشد»       | ffmpeg را نصب کنید و آن را به PATH بیفزایید.                                               |
| هیچ تعریفی یافت نشد     | یک واژه‌نامه Yomitan را در تنظیمات ← واژه‌نامه‌ها ← افزودن واژه‌نامه… اضافه کنید. |
| نصب‌کننده Windows باز نمی‌شود / هشدار SmartScreen | [نکته‌های اجرای نخست](#نکتههای-اجرای-نخست-نسخههای-امضانشده) را ببینید: **More info** -> **Run anyway** را انتخاب کنید؛ هشدارهای نادرست Defender را از **Protection history** بازیابی کنید. |
| نصب تازه هیچ تعریفی ندارد | ابزارها ← راهنمای راه‌اندازی یا ابزارها ← دانلود منابع پیشنهادی را اجرا کنید. برای وارد کردن دستی، فایل ZIP مربوط به Yomitan را دست‌نخورده نگه دارید (آن را از حالت فشرده خارج نکنید). |
| افزودن واژه‌نامه متوقف یا ناموفق می‌شود | آخرین مرحله نمایان را یادداشت کنید و گزارش‌ها را پیوست کنید (بخش «گزارش‌ها کجا هستند؟» در پایین را ببینید). نام، منبع و اندازه فایل ZIP واژه‌نامه را در گزارش بیاورید. |
| گزارش‌ها کجا هستند؟      | از راهنما ← باز کردن پوشه گزارش‌ها استفاده کنید، یا در Windows فایل `%USERPROFILE%\.anki_miner\anki_miner.log` و در macOS/Linux فایل `~/.anki_miner/anki_miner.log` را باز کنید. گزارش‌های چرخشی پسوندهای `.1` تا `.5` دارند. اگر `anki_miner.crash` وجود دارد آن را هم بفرستید (فروپاشی‌ای که برنامه را از کار انداخته، پشته خود را در آن فایل می‌نویسد، نه در گزارش)، و نیز `anki_miner.child.log` را که خروجی یک فرایند کمکی را در خود دارد. |
| گزارش اشکال          | راهنما ← صدور اطلاعات عیب‌یابی… یک فایل ZIP در مکانی که انتخاب می‌کنید می‌نویسد که شامل گزارش‌ها (`anki_miner.log` و نسخه‌های چرخشی آن، `anki_miner.crash`، `anki_miner.child.log`)، فایل `settings.json` شما، فایل‌های پیکربندی و وضعیت رابط، تصویرهای لحظه‌ای صف و فهرست‌های دانلود، و گزارش‌های تولیدشده از وضعیت دستگاه و برنامه (`environment.txt`، `health.txt`، `resources.txt`، `stores.txt`، `disk.txt`، `screens.txt`) است. پیش از بارگذاری آن را بازبینی کنید، چون مسیرها و نام فایل‌های رایانه شما را در خود دارد. هیچ چیز به‌طور خودکار بارگذاری نمی‌شود. |
| گزارش‌گیری عیب‌یابی بیشتر | پیش از اجرای Anki Miner، `ANKI_MINER_LOG_LEVEL=DEBUG` را تنظیم کنید تا جزئیات کتابخانه‌های جانبی yt-dlp، urllib3 و fugashi ثبت شود. مقدار پیش‌فرض `WARNING` است؛ گزارش‌های خود Anki Miner در سطح DEBUG می‌مانند. |
| زبان صوت اشتباه است  | ابزار ترک صوتی زبان استخراج را انتخاب می‌کند و در غیر این صورت نخستین ترک را. خودتان آن را با «ترک صوتی…» (ویدیو ← تکی) انتخاب کنید. |
| زیرنویس‌ها همگام نیستند    | از کنترل جابه‌جایی زیرنویس در رابط کاربری استفاده کنید (بازه ±300 ثانیه).                 |

## مشارکت

هر نوع مشارکتی خوشایند است.
اگر می‌خواهید از پروژه حمایت کنید، لطفاً آن را با دیگرانی که ممکن است از آن بهره ببرند به اشتراک بگذارید.

- تازه‌وارد هستید؟ از [CONTRIBUTING.md](../CONTRIBUTING.md) شروع کنید.
- نمای کلی معماری: [ARCHITECTURE.md](../ARCHITECTURE.md).
- آیین‌نامه رفتاری: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- امنیت: [SECURITY.md](../SECURITY.md).

گزارش اشکال و درخواست ویژگی -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
پرسش‌های عمومی و گفت‌وگو -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) یا [Discord](https://discord.com/invite/aDtQyZzUVP).

## سپاس ویژه

صمیمانه از کسانی که مشارکت‌های استثنایی در پروژه داشته‌اند سپاسگزاریم:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - پیشنهادهای درخشان برای ویژگی‌ها، آزمایش نسخه‌های جدید، ساختن جامعه کاربری.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - پیشنهادهای عالی برای ویژگی‌ها، ساختن جامعه کاربری و مدیریت آن در Discord.

برای دیدن همه کسانی که به هر شکلی در پروژه مشارکت کرده‌اند، [CONTRIBUTORS.md](../CONTRIBUTORS.md) را ببینید.


## مجوز

GNU General Public License v3.0. [LICENSE](../LICENSE) را ببینید.

</div>
