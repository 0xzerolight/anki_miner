<!-- i18n-source: README.md sha256:ecd0ab0242935ee3 -->

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
<div align="center">
<details>
<summary>שפה: עברית</summary>
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
<b>עברית</b> ·
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
הופכים תוכן מקורי ביפנית, בסינית, בקוריאנית ובעוד עשרים ותשע שפות לכרטיסי אוצר מילים של Anki.
</p>

<p align="center">
זמין גם ל-Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner ל-Android</a>.
</p>

<p align="center">
אם Anki Miner עזר לכם, נשמח לכוכב ⭐ - זה עוזר לאחרים למצוא אותו :).
</p>


# <p align="center">הדגמת כרייה</p>

![הדגמה של Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">הדגמה מלאה עם קול (MP4)</a></p>

### כרטיסים לדוגמה

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (עם קול)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (עם קול)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (עם קול)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## התקנה

### דרישות

- **Anki** עם התוסף [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (קוד `2055492159`)
- **ffmpeg** + **libmpv** (לתצוגה מקדימה של וידאו בלבד) - נדרשים רק בהתקנה דרך pip/pipx או מקוד המקור.

את קובץ ההורדה לפלטפורמה שלכם אפשר להוריד מ[הגרסה האחרונה](https://github.com/0xzerolight/anki_miner/releases/latest):

| פלטפורמה | הורדה |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (אחר) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ ללא צילומי מסך בפורמט AVIF וללא הסרת שקט בכתוביות שנוצרות.

### הערות להפעלה הראשונה (גרסאות לא חתומות)

- **macOS**: פותחים את קובץ ה-`.dmg`, גוררים את **AnkiMiner** אל **Applications** ומפעילים. ההפעלה הראשונה נחסמת כי היישום לא עבר notarization: סוגרים את תיבת הדו-שיח, פותחים את היישום שוב ובוחרים **Open Anyway** (נמצא גם תחת **System Settings** -> **Privacy & Security**). פעם אחת בלבד.
- **Linux AppImage**: יש להפוך את הקובץ להרצה לפני ההפעלה - `chmod +x AnkiMiner-*.AppImage`, או **Properties** -> **Allow executing** במנהל הקבצים.
- **Windows SmartScreen**: **More info** -> **Run anyway**.
- **התרעת שווא של Windows Defender**: לשחזר מ-**Protection history** או [לדווח ל-Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>התקנה מ-PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

יפנית, אינדונזית ועברית לא צריכות שום תוספת. לשפת כרייה אחרת יש להוסיף את המנוע שלה:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

לערבית ולפרסית אין תוספת. הנתונים שלהן, וכן מודלי ה-spaCy של השפות האירופיות, מורדים מתוך היישום בהגדרות ← שפת כרייה, שם ההורדות שלמעלה מביאות הכול.

</details>

<details>
<summary><strong>התקנה מקוד המקור</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

להגדרת סביבת פיתוח מלאה, ראו [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## לשוניות

- **וידאו** - כריית זוג וידאו/כתוביות יחיד, תיקיית אצווה, כתובות URL של YouTube או Bilibili, או סדרה שלמה לחפיסה אחת המדורגת לפי תדירות.
- **ספרי שמע** - כריית ספרי שמע, פודקאסטים, רדיו ושירים (זוגות של שמע + כתוביות/תמליל).
- **קריאה** - כריית מנגה (mokuro), רומנים (`.epub`, `.txt`; ספר יחיד או תיקייה שלמה), קובצי כתוביות עצמאיים או טקסט מודבק.
- **ניתוח** - היסטוריית כרייה, דירוגי קושי, אבני דרך.
- **עזרים** - יצירת כתוביות (Whisper מקומי), תזמון כתוביות מחדש (ffsubsync/alass), דחיסת מדיה לשמע של דיאלוג בלבד, הורדת וידאו/שמע/כתוביות מכל אתר ש-yt-dlp תומך בו, העתקת החלק ששווה ללמוד מחפיסה מוכנה לחפיסה חדשה, השלמת שדות בכרטיסים קיימים, OCR לתמונות עמודי מנגה לקובצי .mokuro ‏(mokuro), ותזמון ספר שמע לטקסט של הספר (סנכרון ספרים).
- **הגדרות** - כל מה שניתן להגדרה.

## תכונות נוספות

- שפות כרייה - יפנית, סינית, קוריאנית, אנגלית, קטלאנית, גרמנית, פורטוגזית, צרפתית, ספרדית, איטלקית, הולנדית, נורווגית (בוקמול), רומנית, יוונית, פינית, הונגרית, קרואטית, שוודית, פולנית, ליטאית, דנית, טורקית, אינדונזית, רוסית, ערבית, תאית, פרסית, סלובנית, אוקראינית, וייטנאמית, קנטונזית ועברית, עם מעבר ביניהן בהגדרות. שפות מלבד יפנית מורידות את המנוע שלהן מתוך היישום; אינדונזית ועברית לא צריכות מנוע.
- בורר המילים - סקירה של כל מילה מועמדת לפני יצירת הכרטיסים, עם הסצנה, עמוד המנגה והערך במילון זה לצד זה.
- ביטול הרצה - מחיקת הפתקים שהרצה יצרה זה עתה, ישירות מחלון התוצאות שלה.
- סינון נרחב: i+1, טווח דירוג תדירות, רשימת חסימה, ביטויים רגולריים, ערכות מילים ועוד.
- ייבוא מילוני Yomitan לא מקוונים - הגדרות, הטעמת גובה, תדירות - בשרשרת לפי עדיפות.
- רשימות תדירות מרובות בשרשרת לפי עדיפות.
- שמע מילים בכרטיסים מחבילות שמע מקומיות, JapanesePod101,‏ Google TTS או Microsoft Edge TTS.
- שמע משפטים בכרטיסי קריאה מ-Google Translate TTS, או מ-Naver Papago ליפנית ולקוריאנית (כבוי כברירת מחדל).
- עיצוב הגדרות נפרד לכל מילון, בסגנון Yomitan.
- תצוגה מקדימה מוטמעת של וידאו עם libmpv - ניגון הסצנה של מילה בזמן הבחירה, או כוונון תזמון הכתוביות עם ניגון חי.
- צילומי מסך מונפשים (ראו את הכרטיסים לדוגמה למעלה).
- פרופילי הגדרות - שמירת תצורות בעלות שם ומעבר ביניהן מהכותרת העליונה.
- עיצוב מחדש של כרטיסים שנכרו - החלה מחדש של עיצוב הכרטיסים הנוכחי על כרטיסים שכבר נוצרו (עזרים ← השלמת כרטיסים).

<details>
<summary><strong>ערכות נושא מובנות (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (בהירה); Frappé, Macchiato, Mocha (כהות)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (בהירה), Wave (כהה)
- **Rosé Pine** - Dawn (בהירה); Main, Moon (כהות)
- **Solarized** - Light, Dark
- **עצמאיות** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

רישיונות ערכות הנושא: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
רוצים שתתווסף ערכת נושא נוספת? אפשר להציע ב-GitHub Issue.

</details>

<details>
<summary><strong>איך זה עובד</strong></summary>

1. **קריאת הכתוביות** ופיצול הטקסט למילים בודדות.
2. **סינון** למילות תוכן שעדיין אינן מוכרות לכם - עם אפשרות לסקור את הרשימה בעצמכם בבורר המילים.
3. **לכידת צילום מסך וקטע שמע** מהסרטון לכל שורה.
4. **חיפוש הגדרות** במילונים הלא מקוונים שהוגדרו.
5. **שליחת הכרטיסים המוכנים ל-Anki.**

</details>

## משאבים מומלצים

מילונים ורשימות תדירות לכל שפת כרייה, עם קישורי הורדה: [RESOURCES.md](../RESOURCES.md). אשף ההגדרה מציע את הערכה המתאימה לשפת הכרייה שלכם.

<details>
<summary><strong>הרישיון של JMnedict</strong></summary>

נעשה שימוש בערכות שמות מצורפות שנגזרו מ-[JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (פרויקט JMdict/EDICT,‏ EDRDG,‏ CC BY-SA 4.0).

</details>

## פתרון בעיות

| בעיה | פתרון |
|--------------------------|----------------------------------------------------------------------------------|
| "Cannot connect to Anki" | יש להפעיל את Anki ולוודא ש-AnkiConnect מותקן. |
| "Deck not found" | יש לבחור חפיסה קיימת בהגדרות ← כרטיסים ו-Anki. חפיסות אינן נוצרות אוטומטית; אם צריך חפיסה חדשה, יש ליצור אותה קודם ב-Anki. |
| "Note type not found" | יש להגדיר את שמות השדות של סוג הפתק בהגדרות ← כרטיסים ו-Anki. |
| "ffmpeg not found" | יש להתקין את ffmpeg ולהוסיף אותו ל-PATH. |
| לא נמצאו הגדרות | יש להוסיף מילון Yomitan בהגדרות ← מילונים ← להוסיף מילון… |
| תוכנית ההתקנה של Windows לא נפתחת / אזהרת SmartScreen | ראו [הערות להפעלה הראשונה](#הערות-להפעלה-הראשונה-גרסאות-לא-חתומות): לבחור **More info** -> **Run anyway**; התרעות שווא של Defender משחזרים מ-**Protection history**. |
| בהתקנה חדשה אין הגדרות | יש להריץ את כלים ← אשף ההגדרה או את כלים ← להוריד משאבים מומלצים. בייבוא ידני, יש להשאיר את קובץ ה-ZIP של Yomitan שלם (לא לחלץ אותו). |
| הוספת מילון נתקעת או נכשלת | יש לרשום את השלב האחרון שהוצג ולצרף יומנים (ראו "איפה היומנים?" למטה). בדיווח יש לציין את שם קובץ ה-ZIP של המילון, את המקור שלו ואת גודלו. |
| איפה היומנים? | עזרה ← לפתוח את תיקיית היומנים, או לפתוח את `%USERPROFILE%\.anki_miner\anki_miner.log` ב-Windows או את `~/.anki_miner/anki_miner.log` ב-macOS/Linux. יומנים ישנים מקבלים את הסיומות `.1` עד `.5`. אם קיים גם `anki_miner.crash`, יש לשלוח גם אותו (קריסה שהפילה את היישום כותבת את מחסנית הקריאות שלה לקובץ הזה, ולא ליומן), וגם את `anki_miner.child.log`, שמכיל את הפלט של תהליך עזר. |
| דיווח על באג | עזרה ← לייצא נתוני אבחון… כותב קובץ ZIP למיקום שבוחרים, ובו היומנים (`anki_miner.log` והגרסאות הישנות שלו, `anki_miner.crash`, `anki_miner.child.log`), קובץ `settings.json`, קובצי התצורה ומצב הממשק, תמונות מצב של התורים ומניפסטים של הורדות, ודוחות שנוצרו על מצב המחשב והיישום (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). כדאי לעבור עליו לפני העלאה, כי הוא מכיל נתיבים ושמות קבצים מהמחשב שלכם. שום דבר לא מועלה אוטומטית. |
| רישום אבחון מפורט יותר | יש להגדיר `ANKI_MINER_LOG_LEVEL=DEBUG` לפני הפעלת Anki Miner כדי לתעד פרטים מרכיבי צד שלישי: yt-dlp,‏ urllib3 ו-fugashi. ברירת המחדל היא `WARNING`; היומנים של Anki Miner עצמו נשארים ברמת DEBUG. |
| השמע בשפה הלא נכונה | הכלי בוחר את רצועת השמע של שפת הכרייה, ואם אין כזו – את הראשונה. אפשר לבחור אותה בעצמכם דרך "רצועת שמע…" (וידאו ← יחיד). |
| הכתוביות לא מסונכרנות | יש להשתמש בפקד היסט הכתוביות בממשק (טווח ±300 שניות). |

## תרומה לפרויקט

תרומות מכל סוג יתקבלו בברכה.
אם תרצו לתמוך בפרויקט, שתפו אותו עם אחרים שעשויים להפיק ממנו תועלת.

- חדשים כאן? התחילו ב-[CONTRIBUTING.md](../CONTRIBUTING.md).
- סקירת הארכיטקטורה: [ARCHITECTURE.md](../ARCHITECTURE.md).
- קוד התנהגות: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- אבטחה: [SECURITY.md](../SECURITY.md).

דיווחי באגים ובקשות לתכונות ← [Issues](https://github.com/0xzerolight/anki_miner/issues).
שאלות כלליות ודיונים ← [Discussions](https://github.com/0xzerolight/anki_miner/discussions) או [Discord](https://discord.com/invite/aDtQyZzUVP).

## תודות מיוחדות

תודה כנה לאנשים שתרמו תרומה יוצאת דופן לפרויקט:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - הצעות מבריקות לתכונות, בדיקת גרסאות חדשות, בניית הקהילה.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - הצעות מצוינות לתכונות, בניית הקהילה וניהול ב-Discord.

ב-[CONTRIBUTORS.md](../CONTRIBUTORS.md) מופיעים כל מי שתרמו לפרויקט בכל דרך שהיא.


## רישיון

GNU General Public License v3.0. ראו [LICENSE](../LICENSE).

</div>
