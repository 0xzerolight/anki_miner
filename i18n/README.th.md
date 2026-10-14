<!-- i18n-source: README.md sha256:570dfcaa73bf51ed -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="เวอร์ชันบน PyPI"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="สัญญาอนุญาต: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="ยอดดาวน์โหลดบน GitHub"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="ดาวบน GitHub"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="ชุมชน Discord"></a>
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
<b>ไทย</b> ·
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
<a href="README.el.md">Ελληνικά</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
เปลี่ยนสื่อต้นฉบับภาษาญี่ปุ่น จีน เกาหลี และอีกยี่สิบเก้าภาษาให้เป็นการ์ดคำศัพท์ Anki
</p>

<p align="center">
มีบน Android ด้วย - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner สำหรับ Android</a>
</p>

<p align="center">
หาก Anki Miner ช่วยคุณได้ โปรดกด ⭐ ให้ดาว - เพื่อให้คนอื่นค้นพบได้ง่ายขึ้น :)
</p>


# <p align="center">ตัวอย่างการขุดคำ</p>

![ตัวอย่างการใช้งาน Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">วิดีโอสาธิตฉบับเต็มพร้อมเสียง (MP4)</a></p>

### ตัวอย่างการ์ด

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (มีเสียง)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (มีเสียง)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (มีเสียง)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## การติดตั้ง

### สิ่งที่ต้องมี

- **Anki** พร้อมส่วนเสริม [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (รหัส `2055492159`)
- **ffmpeg** + **libmpv** (สำหรับการแสดงตัวอย่างวิดีโอเท่านั้น) - จำเป็นเฉพาะเมื่อติดตั้งผ่าน pip/pipx หรือจากซอร์สโค้ด

ดาวน์โหลดไฟล์สำหรับแพลตฟอร์มของคุณจาก[รุ่นล่าสุด](https://github.com/0xzerolight/anki_miner/releases/latest):

| แพลตฟอร์ม | ดาวน์โหลด |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (อื่นๆ) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ ไม่มีภาพหน้าจอ AVIF และการตัดช่วงเงียบสำหรับคำบรรยายที่สร้างขึ้น

### หมายเหตุการเปิดใช้ครั้งแรก (บิลด์ที่ไม่ได้ลงนาม)

- **macOS**: เปิดไฟล์ `.dmg` ลาก **AnkiMiner** ไปที่ **แอปพลิเคชัน** แล้วเปิดแอป การเปิดครั้งแรกจะถูกบล็อกเพราะแอปไม่ได้ผ่านการรับรอง (notarize): ปิดกล่องโต้ตอบ เปิดแอปอีกครั้ง แล้วเลือก **เปิดต่อไป** (มีใน **การตั้งค่าระบบ** -> **ความเป็นส่วนตัวและความปลอดภัย** ด้วย) ทำเพียงครั้งเดียว
- **Linux AppImage**: ทำให้ไฟล์เรียกใช้ได้ก่อนเปิด - `chmod +x AnkiMiner-*.AppImage` หรือ **คุณสมบัติ** -> **อนุญาตให้เรียกใช้** ในตัวจัดการไฟล์
- **Windows SmartScreen**: **ข้อมูลเพิ่มเติม** -> **เรียกใช้ต่อไป**
- **Windows Defender ตรวจพบผิดพลาด**: กู้คืนจาก **ประวัติการป้องกัน** หรือ[รายงานไปยัง Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission)

<details>
<summary><strong>ติดตั้งจาก PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

ภาษาญี่ปุ่น อินโดนีเซีย และฮีบรูไม่ต้องติดตั้งอะไรเพิ่ม สำหรับภาษาที่ขุดคำอื่น ให้เพิ่มเอนจินของภาษานั้น:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

ภาษาอาหรับและเปอร์เซียไม่มีชุดเสริม ข้อมูลของสองภาษานี้และโมเดล spaCy ของภาษายุโรปจะดาวน์โหลดภายในแอปจาก การตั้งค่า -> ภาษาที่ขุดคำ ซึ่งการดาวน์โหลดที่นั่นจะดึงทุกอย่างที่ชุดเสริมด้านบนให้มา

</details>

<details>
<summary><strong>ติดตั้งจากซอร์สโค้ด</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

สำหรับการตั้งค่าสภาพแวดล้อมการพัฒนาแบบเต็ม ดูที่ [CONTRIBUTING.md](../CONTRIBUTING.md)

</details>

## แท็บ

- **วิดีโอ** - ขุดคำจากคู่วิดีโอ/คำบรรยายหนึ่งคู่ โฟลเดอร์แบบชุด URL ของ YouTube หรือ Bilibili หรือทั้งซีรีส์ให้เป็นสำรับเดียวที่เรียงตามความถี่
- **หนังสือเสียง** - ขุดคำจากหนังสือเสียง พอดแคสต์ วิทยุ เพลง (คู่เสียง + คำบรรยาย/บทถอดความ)
- **การอ่าน** - ขุดคำจากมังงะ (mokuro) นิยาย (`.epub`, `.txt`; หนังสือเล่มเดียวหรือทั้งโฟลเดอร์) ไฟล์คำบรรยายเดี่ยว หรือข้อความที่วาง
- **สถิติ** - ประวัติการขุดคำ อันดับความยาก เป้าหมายที่ทำได้
- **ยูทิลิตี้** - สร้างคำบรรยาย (Whisper ในเครื่อง) ปรับเวลาคำบรรยาย (ffsubsync/alass) ย่อสื่อให้เหลือเฉพาะเสียงบทพูด ดาวน์โหลดวิดีโอ/เสียง/คำบรรยายจากทุกเว็บไซต์ที่ yt-dlp รองรับ คัดลอกส่วนที่น่าเรียนของสำรับสำเร็จรูปไปยังสำรับใหม่ เติมฟิลด์ในการ์ดที่มีอยู่ ทำ OCR ภาพหน้ามังงะเป็นไฟล์ .mokuro (mokuro) และจับเวลาหนังสือเสียงให้ตรงกับข้อความของหนังสือ (ซิงก์หนังสือเสียง)
- **การตั้งค่า** - ทุกอย่างที่ปรับแต่งได้

## ฟีเจอร์อื่นๆ

- ภาษาที่ขุดคำ - ญี่ปุ่น จีน เกาหลี อังกฤษ คาตาลัน เยอรมัน โปรตุเกส ฝรั่งเศส สเปน อิตาลี ดัตช์ นอร์เวย์ (บุ๊กมอล) โรมาเนีย กรีก ฟินแลนด์ ฮังการี โครเอเชีย สวีเดน โปแลนด์ ลิทัวเนีย เดนมาร์ก ตุรกี อินโดนีเซีย รัสเซีย อาหรับ ไทย เปอร์เซีย สโลวีเนีย ยูเครน เวียดนาม กวางตุ้ง และฮีบรู สลับได้ในการตั้งค่า ภาษาอื่นนอกจากญี่ปุ่นจะดาวน์โหลดเอนจินภายในแอป ยกเว้นอินโดนีเซียและฮีบรูที่ไม่ต้องใช้
- ตัวคัดคำ - ตรวจทานคำที่เป็นตัวเลือกทุกคำก่อนสร้างการ์ด พร้อมฉาก หน้ามังงะ และรายการในพจนานุกรมแสดงเคียงกัน
- เลิกทำรอบ - ลบโน้ตที่รอบหนึ่งเพิ่งสร้างได้ทันทีจากหน้าต่างผลลัพธ์ของรอบนั้น
- การกรองที่ครอบคลุม: i+1 ช่วงอันดับความถี่ รายการห้าม regex ชุดคำ และอื่นๆ
- นำเข้าพจนานุกรม Yomitan แบบออฟไลน์ - ความหมาย การเน้นระดับเสียง ความถี่ - เรียงต่อกันตามลำดับความสำคัญ
- รายการความถี่หลายรายการเรียงต่อกันตามลำดับความสำคัญ
- เสียงคำศัพท์บนการ์ดจากชุดเสียงในเครื่อง JapanesePod101, Google TTS หรือ Microsoft Edge TTS
- เสียงประโยคบนการ์ดจากการอ่านผ่าน Google Translate TTS หรือ Naver Papago สำหรับภาษาญี่ปุ่นและเกาหลี (ปิดไว้เป็นค่าเริ่มต้น)
- จัดรูปแบบอภิธานศัพท์แยกตามพจนานุกรมแบบเดียวกับ Yomitan
- แสดงตัวอย่างวิดีโอด้วย libmpv ในตัว - เล่นฉากของคำขณะคัดคำ หรือขยับเวลาคำบรรยายพร้อมดูการเล่นสด
- ภาพหน้าจอแบบเคลื่อนไหว (ดูตัวอย่างการ์ดด้านบน)
- โปรไฟล์การตั้งค่า - บันทึกการกำหนดค่าเป็นชื่อต่างๆ และสลับไปมาได้จากส่วนหัวของแอป
- จัดรูปแบบการ์ดที่ขุดใหม่ - นำรูปแบบการ์ดปัจจุบันไปใช้กับการ์ดที่คุณสร้างไว้แล้วอีกครั้ง (ยูทิลิตี้ → เติมการ์ด)

<details>
<summary><strong>ธีมในตัว (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (สว่าง); Frappé, Macchiato, Mocha (มืด)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (สว่าง), Wave (มืด)
- **Rosé Pine** - Dawn (สว่าง); Main, Moon (มืด)
- **Solarized** - Light, Dark
- **ธีมเดี่ยว** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

สัญญาอนุญาตของธีม: [LICENSE-THEMES.md](../LICENSE-THEMES.md) 
อยากให้เพิ่มธีมอื่นหรือไม่? เสนอได้ใน GitHub Issue

</details>

<details>
<summary><strong>วิธีการทำงาน</strong></summary>

1. **อ่านคำบรรยาย** และแยกข้อความออกเป็นคำแต่ละคำ
2. **กรอง** ให้เหลือคำเนื้อหาที่คุณยังไม่รู้ - และตรวจทานรายการด้วยตัวเองในตัวคัดคำได้หากต้องการ
3. **ถ่ายภาพหน้าจอและตัดคลิปเสียง** จากวิดีโอสำหรับแต่ละบรรทัด
4. **ค้นหาความหมาย** ในพจนานุกรมออฟไลน์ที่คุณตั้งค่าไว้
5. **ส่งการ์ดที่เสร็จแล้วไปยัง Anki**

</details>

## ทรัพยากรที่แนะนำ

พจนานุกรมและรายการความถี่สำหรับทุกภาษาที่ขุดคำ พร้อมลิงก์ดาวน์โหลด: [RESOURCES.md](../RESOURCES.md) ตัวช่วยตั้งค่าจะเสนอชุดที่เหมาะกับภาษาที่ขุดคำของคุณ

<details>
<summary><strong>สัญญาอนุญาตของ JMnedict</strong></summary>

ใช้ชุดคำชื่อเฉพาะที่มาพร้อมโปรแกรมซึ่งสร้างจาก [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (โครงการ JMdict/EDICT, EDRDG, CC BY-SA 4.0)

</details>

## การแก้ไขปัญหา

| ปัญหา                    | วิธีแก้                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| "เชื่อมต่อ Anki ไม่ได้" | เปิด Anki และตรวจสอบว่าติดตั้ง AnkiConnect แล้ว                                  |
| "ไม่พบสำรับ"         | เลือกสำรับที่มีอยู่ใน การตั้งค่า -> การ์ดและ Anki แอปจะไม่สร้างสำรับให้ หากต้องการสำรับใหม่ ให้สร้างใน Anki ก่อน |
| "ไม่พบประเภทโน้ต"    | กำหนดชื่อฟิลด์ของประเภทโน้ตใน การตั้งค่า -> การ์ดและ Anki               |
| "ไม่พบ ffmpeg"       | ติดตั้ง ffmpeg และเพิ่มลงใน PATH                                               |
| ไม่พบความหมาย     | เพิ่มพจนานุกรม Yomitan ใน การตั้งค่า -> พจนานุกรม -> เพิ่มพจนานุกรม… |
| ตัวติดตั้ง Windows ไม่เปิด / มีคำเตือน SmartScreen | ดู[หมายเหตุการเปิดใช้ครั้งแรก](#หมายเหตุการเปิดใช้ครั้งแรก-บิลด์ที่ไม่ได้ลงนาม): เลือก **ข้อมูลเพิ่มเติม** -> **เรียกใช้ต่อไป** และกู้คืนไฟล์ที่ Defender ตรวจพบผิดพลาดจาก **ประวัติการป้องกัน** |
| ติดตั้งใหม่แล้วไม่มีความหมาย | เรียกใช้ เครื่องมือ -> ตัวช่วยตั้งค่า หรือ เครื่องมือ -> ดาวน์โหลดทรัพยากรที่แนะนำ หากนำเข้าเอง ให้ใช้ไฟล์ ZIP ของ Yomitan ตามเดิม (ไม่ต้องแตกไฟล์) |
| การเพิ่มพจนานุกรมค้างหรือล้มเหลว | จดขั้นตอนสุดท้ายที่เห็นและแนบไฟล์บันทึก (ดู "ไฟล์บันทึกอยู่ที่ไหน?" ด้านล่าง) ระบุชื่อ แหล่งที่มา และขนาดของไฟล์ ZIP พจนานุกรมในรายงานด้วย |
| ไฟล์บันทึกอยู่ที่ไหน?      | ใช้ ช่วยเหลือ -> เปิดโฟลเดอร์บันทึก หรือเปิด `%USERPROFILE%\.anki_miner\anki_miner.log` บน Windows หรือ `~/.anki_miner/anki_miner.log` บน macOS/Linux ไฟล์บันทึกที่หมุนเวียนจะมีนามสกุล `.1` ถึง `.5` ส่ง `anki_miner.crash` มาด้วยหากมี (การขัดข้องที่ทำให้แอปปิดตัวจะเขียนสแตกลงในไฟล์นั้น ไม่ใช่ในไฟล์บันทึก) และ `anki_miner.child.log` ซึ่งเก็บเอาต์พุตของโปรเซสตัวช่วย |
| การรายงานข้อบกพร่อง          | ช่วยเหลือ -> ส่งออกข้อมูลวินิจฉัย… จะเขียนไฟล์ ZIP ไปยังตำแหน่งที่คุณเลือก ซึ่งมีไฟล์บันทึก (`anki_miner.log` และไฟล์หมุนเวียน, `anki_miner.crash`, `anki_miner.child.log`) ไฟล์ `settings.json` ไฟล์การกำหนดค่าและสถานะ UI สแนปช็อตคิวและรายการไฟล์ดาวน์โหลด และรายงานสถานะเครื่องและแอปที่สร้างขึ้น (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`) ตรวจดูก่อนอัปโหลด เพราะมีพาธและชื่อไฟล์จากคอมพิวเตอร์ของคุณ ไม่มีสิ่งใดถูกอัปโหลดโดยอัตโนมัติ |
| บันทึกข้อมูลวินิจฉัยเพิ่มเติม | ตั้งค่า `ANKI_MINER_LOG_LEVEL=DEBUG` ก่อนเปิด Anki Miner เพื่อเก็บรายละเอียดจาก yt-dlp, urllib3 และ fugashi ค่าเริ่มต้นคือ `WARNING` ส่วนบันทึกของ Anki Miner ยังคงอยู่ที่ DEBUG |
| เสียงเป็นภาษาผิด  | เครื่องมือจะเลือกแทร็กเสียงของภาษาที่ขุดคำ หากไม่มีจะใช้แทร็กแรก เลือกเองได้ด้วย แทร็กเสียง… (วิดีโอ -> เดี่ยว) |
| คำบรรยายไม่ตรงเวลา    | ใช้ตัวควบคุมเลื่อนเวลาคำบรรยายในแอป (ช่วง ±300 วินาที)                 |

## การมีส่วนร่วม

ยินดีรับการมีส่วนร่วมทุกรูปแบบ
หากคุณต้องการสนับสนุนโครงการ โปรดแบ่งปันให้คนอื่นที่อาจได้ประโยชน์

- มาใหม่ใช่ไหม? เริ่มจาก [CONTRIBUTING.md](../CONTRIBUTING.md)
- ภาพรวมสถาปัตยกรรม: [ARCHITECTURE.md](../ARCHITECTURE.md)
- หลักปฏิบัติ: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)
- ความปลอดภัย: [SECURITY.md](../SECURITY.md)

รายงานข้อบกพร่องและคำขอฟีเจอร์ -> [Issues](https://github.com/0xzerolight/anki_miner/issues)
คำถามทั่วไปและการพูดคุย -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) หรือ [Discord](https://discord.com/invite/aDtQyZzUVP)

## ขอขอบคุณเป็นพิเศษ

ขอขอบคุณอย่างจริงใจต่อผู้ที่มีส่วนร่วมกับโครงการอย่างโดดเด่น:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - ข้อเสนอฟีเจอร์ที่ยอดเยี่ยม การทดสอบรุ่นใหม่ และการสร้างชุมชน
- ★ **[rob-olvr](https://github.com/rob-olvr)** - ข้อเสนอฟีเจอร์ที่ดีเยี่ยม การสร้างชุมชน และการดูแลบน Discord

ดู [CONTRIBUTORS.md](../CONTRIBUTORS.md) สำหรับทุกคนที่มีส่วนร่วมกับโครงการในรูปแบบใดก็ตาม


## สัญญาอนุญาต

GNU General Public License v3.0 ดู [LICENSE](../LICENSE)
