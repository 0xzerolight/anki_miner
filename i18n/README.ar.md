<!-- i18n-source: README.md sha256:6f9163baa7a87e06 -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="إصدار PyPI"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="الترخيص: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="تنزيلات GitHub"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="نجوم GitHub"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="مجتمع Discord"></a>
</p>

<!-- i18n-nav:start -->
<div align="center">
<details>
<summary>اللغة: العربية</summary>
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
<b>العربية</b> ·
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
<a href="README.ro.md">Română</a> ·
<a href="README.tr.md">Türkçe</a>
</p>
</details>
</div>
<!-- i18n-nav:end -->

<div dir="rtl">

<p align="center">
حوّل المحتوى الأصلي باليابانية والصينية والكورية وتسع وعشرين لغة أخرى إلى بطاقات مفردات في Anki.
</p>

<p align="center">
متاح أيضًا على Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner لنظام Android</a>.
</p>

<p align="center">
إذا ساعدك Anki Miner فامنحه ⭐ نجمة - فذلك يساعد الآخرين على العثور عليه :).
</p>


# <p align="center">عرض توضيحي للاستخراج</p>

![عرض Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">العرض الكامل مع الصوت (MP4)</a></p>

### أمثلة على البطاقات

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (مع الصوت)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (مع الصوت)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (مع الصوت)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## التثبيت

### المتطلبات

- **Anki** مع الإضافة [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (الرمز `2055492159`)
- **ffmpeg** + **libmpv** (لمعاينة الفيديو فقط) - مطلوبان فقط عند التثبيت عبر pip/pipx أو من المصدر.

نزّل الملف المناسب لنظامك من [أحدث إصدار](https://github.com/0xzerolight/anki_miner/releases/latest):

| النظام | التنزيل |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (أخرى) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ لا يتضمن لقطات الشاشة بصيغة AVIF ولا إزالة الصمت للترجمات المولّدة.

### ملاحظات التشغيل الأول (إصدارات غير موقّعة)

- **macOS**: افتح ملف `.dmg`، واسحب **AnkiMiner** إلى **التطبيقات**، ثم شغّله. يُحظر التشغيل الأول لأن التطبيق غير موثّق من Apple: أغلق مربع الحوار، وافتح التطبيق مرة أخرى، واختر **الفتح على أي حال** (موجود أيضًا في **إعدادات النظام** ← **الخصوصية والأمن**). مرة واحدة فقط.
- **Linux AppImage**: اجعل الملف قابلًا للتنفيذ قبل تشغيله - `chmod +x AnkiMiner-*.AppImage`، أو **خصائص** ← **السماح بالتنفيذ** في مدير الملفات.
- **Windows SmartScreen**: **مزيد من المعلومات** ← **تشغيل على أي حال**.
- **إنذار كاذب من Windows Defender**: استعد الملف من **محفوظات الحماية** أو [أبلغ Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>التثبيت من PyPI ‏(Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

لا تحتاج اليابانية والإندونيسية والعبرية إلى أي إضافة. لأي لغة استخراج أخرى، أضف محركها:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

ليس للعربية والفارسية إضافة خاصة. تُنزَّل بياناتهما، ونماذج spaCy للغات الأوروبية، من داخل التطبيق عبر إعدادات ← لغة الاستخراج، حيث تجلب التنزيلات المذكورة كل شيء.

</details>

<details>
<summary><strong>التثبيت من المصدر</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

لإعداد بيئة التطوير كاملة، راجع [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## علامات التبويب

- **فيديو** - استخرج من زوج فيديو/ترجمة واحد، أو من مجلد دفعة، أو من روابط YouTube أو Bilibili، أو من مسلسل كامل في رزمة واحدة مرتبة حسب التكرار.
- **كتب صوتية** - استخرج من الكتب الصوتية والبودكاست والراديو والأغاني (أزواج صوت + ترجمة/نص مكتوب).
- **قراءة** - استخرج من المانغا (mokuro)، والروايات (`.epub` و`.txt`؛ كتاب واحد أو مجلد كامل)، وملفات الترجمة المستقلة، أو النص الملصق.
- **تحليلات** - سجل الاستخراج، وترتيب الصعوبة، والإنجازات.
- **مرافق** - ولّد الترجمات (Whisper محليًا)، وأعد توقيت الترجمات (ffsubsync/alass)، وكثّف الوسائط إلى صوت الحوار فقط، ونزّل الفيديو/الصوت/الترجمات من أي موقع يدعمه yt-dlp، وانسخ الجزء الجدير بالتعلّم من رزمة جاهزة إلى رزمة جديدة، وأكمل الحقول في البطاقات الموجودة، وطبّق OCR على صور صفحات المانغا لإنتاج ملفات .mokuro (mokuro)، ووقّت كتابًا صوتيًا على نص كتابه (مزامنة الكتب).
- **إعدادات** - كل ما يمكن ضبطه.

## ميزات أخرى

- لغات الاستخراج - اليابانية والصينية والكورية والإنجليزية والكتالونية والألمانية والبرتغالية والفرنسية والإسبانية والإيطالية والهولندية والنرويجية (بوكمول) والرومانية واليونانية والفنلندية والمجرية والكرواتية والسويدية والبولندية والليتوانية والدنماركية والتركية والإندونيسية والروسية والعربية والتايلاندية والفارسية والسلوفينية والأوكرانية والفيتنامية والكانتونية والعبرية، ويُبدَّل بينها من الإعدادات. تنزّل اللغات غير اليابانية محركها من داخل التطبيق؛ ولا تحتاج الإندونيسية والعبرية إلى محرك.
- منقّح الكلمات - راجع كل كلمة مرشّحة قبل إنشاء البطاقات، مع مشهدها وصفحة المانغا ومُدخل القاموس جنبًا إلى جنب.
- التراجع عن تشغيل - احذف الملاحظات التي أنشأها التشغيل للتو، مباشرة من مربع حوار نتائجه.
- تصفية واسعة: i+1، ونطاق رتبة التكرار، والقائمة السوداء، وregex، ومجموعات الكلمات، والمزيد.
- استيراد قواميس Yomitan دون اتصال - التعريفات والنبر النغمي والتكرار - مرتبة في سلسلة حسب الأولوية.
- قوائم تكرار متعددة مرتبة في سلسلة حسب الأولوية.
- صوت الكلمات على البطاقات من حزم صوت محلية أو JapanesePod101 أو Google TTS أو Microsoft Edge TTS.
- صوت الجملة على بطاقات القراءة من Google Translate TTS، أو Naver Papago لليابانية والكورية (معطّل افتراضيًا).
- تنسيق المسرد لكل قاموس على حدة، على طريقة Yomitan.
- معاينة فيديو مضمّنة عبر libmpv - شغّل مشهد الكلمة أثناء التنقيح، أو اضبط توقيت الترجمة مع تشغيل مباشر.
- لقطات شاشة متحركة (انظر أمثلة البطاقات أعلاه).
- ملفات تعريف الإعدادات - احفظ إعدادات مسمّاة وبدّل بينها من الترويسة.
- إعادة تنسيق البطاقات المستخرجة - أعد تطبيق تنسيق البطاقات الحالي على البطاقات التي أنشأتها مسبقًا؛ أما بطاقات الإصدارات الأقدم فاستخدم لها إكمال البطاقات بدلًا من ذلك (مرافق ← إكمال البطاقات).

<details>
<summary><strong>السمات المدمجة (29)</strong></summary>

- **Ayu** - Light، Mirage، Dark
- **Catppuccin** - Latte (فاتحة)؛ Frappé، Macchiato، Mocha (داكنة)
- **Dracula** - Dracula، Alucard
- **Everforest** - Light، Dark
- **GitHub** - Light؛ Dark، Dark Dimmed
- **Gruvbox** - Light Medium، Dark Medium
- **Kanagawa** - Lotus (فاتحة)، Wave (داكنة)
- **Rosé Pine** - Dawn (فاتحة)؛ Main، Moon (داكنة)
- **Solarized** - Light، Dark
- **مستقلة** - Light، Dark، Sakura، Nord، One Dark، Tokyo Night

تراخيص السمات: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
هل تريد إضافة سمة أخرى؟ اقترحها في GitHub Issue.

</details>

<details>
<summary><strong>آلية العمل</strong></summary>

1. **قراءة الترجمة** وتقسيم النص إلى كلمات منفردة.
2. **التصفية** للإبقاء على كلمات المحتوى التي لا تعرفها بعد - مع إمكانية مراجعة القائمة بنفسك في منقّح الكلمات.
3. **التقاط لقطة شاشة ومقطع صوتي** من الفيديو لكل سطر.
4. **البحث عن التعريفات** في القواميس دون اتصال التي ضبطتها.
5. **إرسال البطاقات الجاهزة إلى Anki.**

</details>

## الموارد الموصى بها

القواميس وقوائم التكرار لكل لغة استخراج، مع روابط التنزيل: [RESOURCES.md](../RESOURCES.md). يقترح معالج الإعداد المجموعة المناسبة للغة الاستخراج لديك.

<details>
<summary><strong>ترخيص JMnedict</strong></summary>

يستخدم مجموعات أسماء مضمّنة مشتقة من [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (مشروع JMdict/EDICT، ‏EDRDG، ‏CC BY-SA 4.0).

</details>

## استكشاف الأخطاء وإصلاحها

| المشكلة                    | الحل                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| «Cannot connect to Anki» | شغّل Anki وتأكّد من تثبيت AnkiConnect.                                  |
| «Deck not found»         | اختر رزمة موجودة في إعدادات ← البطاقات وAnki. لا تُنشأ الرزم تلقائيًا؛ أنشئها في Anki أولًا إذا احتجت إلى رزمة جديدة. |
| «Note type not found»    | اضبط أسماء حقول نوع الملاحظة في إعدادات ← البطاقات وAnki.               |
| «ffmpeg not found»       | ثبّت ffmpeg وأضفه إلى PATH.                                               |
| لم يُعثر على تعريفات     | أضف قاموس Yomitan من إعدادات ← القواميس ← إضافة قاموس… |
| مثبّت Windows لا يفتح / تحذير SmartScreen | راجع [ملاحظات التشغيل الأول](#ملاحظات-التشغيل-الأول-إصدارات-غير-موقّعة): اختر **مزيد من المعلومات** ← **تشغيل على أي حال**؛ واستعد الإنذارات الكاذبة من Defender عبر **محفوظات الحماية**. |
| لا توجد تعريفات بعد تثبيت جديد | شغّل أدوات ← معالج الإعداد أو أدوات ← تنزيل الموارد الموصى بها. للاستيراد اليدوي، أبقِ ملف ZIP الخاص بـ Yomitan كما هو (لا تفك ضغطه). |
| توقف إضافة القاموس أو فشلها | دوّن آخر مرحلة ظاهرة وأرفق السجلات (راجع «أين السجلات؟» أدناه). اذكر في البلاغ اسم ملف ZIP للقاموس ومصدره وحجمه. |
| أين السجلات؟      | استخدم تعليمات ← فتح مجلد السجلات، أو افتح `%USERPROFILE%\.anki_miner\anki_miner.log` على Windows أو `~/.anki_miner/anki_miner.log` على macOS/Linux. تستخدم السجلات المدوّرة اللواحق من `.1` إلى `.5`. أرسل أيضًا `anki_miner.crash` إن وُجد (الانهيار الذي أغلق التطبيق يكتب مكدّسه في هذا الملف، لا في السجل)، و`anki_miner.child.log` الذي يحتوي على مخرجات عملية مساعدة. |
| الإبلاغ عن خطأ          | يكتب تعليمات ← تصدير بيانات التشخيص… ملف ZIP في موقع تختاره، يضم السجلات (`anki_miner.log` ونسخه المدوّرة، و`anki_miner.crash`، و`anki_miner.child.log`)، وملف `settings.json`، وملفات الإعدادات وحالة الواجهة، ولقطات قائمة الانتظار وبيانات التنزيلات، وتقارير مولّدة عن حالة الجهاز والتطبيق (`environment.txt`، `health.txt`، `resources.txt`، `stores.txt`، `disk.txt`، `screens.txt`). راجعه قبل رفعه لأنه يحتوي على مسارات ملفات وأسماء ملفات من حاسوبك. لا يُرفع أي شيء تلقائيًا. |
| مزيد من سجلات التشخيص | اضبط `ANKI_MINER_LOG_LEVEL=DEBUG` قبل تشغيل Anki Miner لالتقاط تفاصيل المكتبات الخارجية yt-dlp وurllib3 وfugashi. القيمة الافتراضية `WARNING`؛ وتبقى سجلات Anki Miner على مستوى DEBUG. |
| الصوت بلغة خاطئة  | تختار الأداة مسار الصوت بلغة الاستخراج، وإلا فالمسار الأول. اختره بنفسك عبر مسار الصوت… (فيديو ← فردي). |
| الترجمة غير متزامنة    | استخدم عنصر التحكم في إزاحة الترجمة في الواجهة (النطاق ±300 ثانية).                 |

## المساهمة

المساهمات من أي نوع مرحّب بها.
إذا أردت دعم المشروع، فشاركه مع من قد يستفيد منه.

- جديد هنا؟ ابدأ بـ [CONTRIBUTING.md](../CONTRIBUTING.md).
- نظرة عامة على البنية: [ARCHITECTURE.md](../ARCHITECTURE.md).
- مدونة السلوك: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- الأمان: [SECURITY.md](../SECURITY.md).

بلاغات الأخطاء وطلبات الميزات ← [Issues](https://github.com/0xzerolight/anki_miner/issues).
الأسئلة العامة والنقاش ← [Discussions](https://github.com/0xzerolight/anki_miner/discussions) أو [Discord](https://discord.com/invite/aDtQyZzUVP).

## شكر خاص

شكر صادق لمن قدّموا مساهمات استثنائية في المشروع:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - اقتراحات ميزات رائعة، واختبار الإصدارات الجديدة، وبناء المجتمع.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - اقتراحات ميزات ممتازة، وبناء المجتمع والإشراف عليه في Discord.

راجع [CONTRIBUTORS.md](../CONTRIBUTORS.md) لمعرفة كل من قدّم أي نوع من المساهمة في المشروع.


## الترخيص

رخصة GNU العمومية العامة، الإصدار 3.0. راجع [LICENSE](../LICENSE).

</div>
