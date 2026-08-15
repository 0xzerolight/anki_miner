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
<a href="README.ru.md">Русский</a> ·
<a href="README.fr.md">Français</a> ·
<a href="README.es.md">Español</a> ·
<a href="README.de.md">Deutsch</a> ·
<a href="README.pt_br.md">Português (Brasil)</a> ·
<a href="README.id.md">Bahasa Indonesia</a> ·
<b>Tiếng Việt</b> ·
<a href="README.zh_cn.md">简体中文</a> ·
<a href="README.zh_tw.md">繁體中文</a> ·
<a href="README.it.md">Italiano</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Biến nội dung tiếng Nhật bản ngữ thành thẻ từ vựng Anki.
</p>

<p align="center">
Cũng có trên Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner cho Android</a>.
</p>

<p align="center">
Hãy để lại một ngôi sao ⭐ nếu Anki Miner giúp ích cho bạn - điều đó giúp người khác tìm thấy nó :).
</p>


# <p align="center">Bản demo khai thác</p>

![Anki Miner Showcase](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Bản demo đầy đủ có âm thanh (MP4)</a></p>

### Thẻ ví dụ

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (âm thanh)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (âm thanh)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (âm thanh)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Cài đặt

### Yêu cầu

- **Anki** cùng add-on [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (mã `2055492159`)
- **ffmpeg** + **libmpv** (chỉ dùng cho xem trước video) - chỉ cần khi cài qua pip/pipx hoặc từ mã nguồn.

Tải bản dành cho nền tảng của bạn từ [bản phát hành mới nhất](https://github.com/0xzerolight/anki_miner/releases/latest):

| Nền tảng | Tải về |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon / M1-M4) | `AnkiMiner-macOS-arm64.tar.gz` |
| macOS (Intel) | `AnkiMiner-macOS-x86_64.tar.gz` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (khác) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Không bao gồm tạo phụ đề bằng Whisper cục bộ và ảnh chụp màn hình AVIF. Để có đầy đủ chức năng: `pipx install "anki-miner[asr]"`.

### Lưu ý lần chạy đầu (bản dựng chưa ký)

- **macOS**: Gatekeeper chặn ứng dụng. Hãy giải nén trước, rồi chạy `xattr -dr com.apple.quarantine AnkiMiner/`
- **Windows SmartScreen**: **More info** -> **Run anyway**.
- **Cảnh báo nhầm của Windows Defender**: khôi phục từ **Protection history** hoặc [báo cho Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Cài đặt từ PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

</details>

<details>
<summary><strong>Cài đặt từ mã nguồn</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Để thiết lập môi trường phát triển đầy đủ, xem [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Các tab

- **Video** - khai thác một cặp video/phụ đề đơn lẻ, một thư mục hàng loạt, hoặc các URL YouTube.
- **Trình dựng bộ thẻ** - khai thác cả một loạt phim thành một bộ thẻ được xếp hạng theo tần suất.
- **Sách nói** - khai thác sách nói, podcast, radio, bài hát (cặp âm thanh + phụ đề/bản chép lời).
- **Cách đọc** - khai thác manga (mokuro), tiểu thuyết (`.epub`, `.txt`; một cuốn sách hoặc cả thư mục), tệp phụ đề độc lập, hoặc văn bản tiếng Nhật dán vào.
- **Phân tích** - lịch sử khai thác, xếp hạng độ khó, cột mốc.
- **Tiện ích** - tạo phụ đề (Whisper cục bộ), căn lại thời gian phụ đề (alass), cô đọng media thành âm thanh chỉ có hội thoại, sao chép phần đáng học của một bộ thẻ dựng sẵn sang một bộ thẻ mới, và điền bổ sung các trường trên thẻ đã có.
- **Cài đặt** - mọi thứ có thể cấu hình.

## Tính năng khác

- Word Curator - xem lại từng từ ứng viên trước khi thẻ được tạo, cùng với cảnh phim, trang manga và mục từ điển của nó đặt cạnh nhau.
- Hoàn tác một lần chạy - xóa các ghi chú mà một lần chạy vừa tạo, ngay trong hộp thoại kết quả.
- Bộ lọc phong phú: i+1, khoảng hạng tần suất, danh sách đen, regex, tập từ, và hơn thế nữa.
- Nhập từ điển Yomitan ngoại tuyến - định nghĩa, trọng âm cao độ, tần suất - xâu chuỗi theo thứ tự ưu tiên.
- Nhiều danh sách tần suất được xâu chuỗi theo thứ tự ưu tiên.
- Âm thanh của từ trên thẻ, lấy từ gói âm thanh cục bộ, JapanesePod101, hoặc Google TTS.
- Âm thanh câu trên thẻ Cách đọc, lấy từ Google Translate TTS hoặc Naver Papago (mặc định tắt).
- Định kiểu bảng nghĩa riêng cho từng từ điển, theo phong cách Yomitan.
- Xem trước video bằng libmpv nhúng - phát cảnh phim của một từ trong lúc chọn lọc, hoặc tinh chỉnh thời gian phụ đề với phát trực tiếp.
- Ảnh chụp màn hình động (xem thẻ ví dụ ở trên).
- Hồ sơ cài đặt - lưu các cấu hình có tên và chuyển đổi giữa chúng từ thanh tiêu đề.
- Tạo lại kiểu cho thẻ đã khai thác - áp dụng lại kiểu thẻ hiện tại của bạn cho những thẻ bạn đã tạo (menu Công cụ).

<details>
<summary><strong>Chủ đề tích hợp sẵn (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (sáng); Frappé, Macchiato, Mocha (tối)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (sáng), Wave (tối)
- **Rosé Pine** - Dawn (sáng); Main, Moon (tối)
- **Solarized** - Light, Dark
- **Độc lập** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Giấy phép chủ đề: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Muốn thêm chủ đề khác? Hãy đề xuất trong một GitHub Issue.

</details>

<details>
<summary><strong>Cách hoạt động</strong></summary>

1. **Đọc phụ đề** và tách tiếng Nhật thành từng từ riêng lẻ.
2. **Lọc** xuống còn các từ mang nghĩa mà bạn chưa biết - tùy chọn tự bạn xem lại danh sách trong Word Curator.
3. **Lấy ảnh chụp màn hình và đoạn âm thanh** từ video cho mỗi dòng.
4. **Tra định nghĩa** trong các từ điển ngoại tuyến bạn đã cấu hình, tùy chọn dự phòng sang Jisho trực tuyến nếu được bật (chậm hơn, bị giới hạn tốc độ).
5. **Gửi các thẻ đã hoàn thiện sang Anki.**

</details>

## Tài nguyên được đề xuất

| Loại | Tài nguyên | Tải về | Thêm qua |
|------|----------|----------|---------|
| Từ điển | [JMdict](https://github.com/yomidevs/jmdict-yomitan) | [Zip Yomitan](https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip) | Thêm từ điển… |
| Từ điển | [Jitendex](https://jitendex.org/) | [Zip Yomitan](https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip) | Thêm từ điển… |
| Từ điển | [Bee's Character Dictionary](https://characterdictionary.tokyo/) | Tạo ngay trên trang | Thêm từ điển… |
| Trọng âm | [Kanjium](https://github.com/mifunetoshiro/kanjium) | [TSV](https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt) | Trọng âm cao độ -> Thêm nguồn trọng âm cao độ… |
| Trọng âm | [アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries) | [Drive](https://drive.google.com/drive/folders/1tTdLppnqMfVC5otPlX_cs4ixlIgjv_lH) | Trọng âm cao độ -> Thêm nguồn trọng âm cao độ… |
| Tần suất | [JPDB v2.2 Kana](https://github.com/Kuuuube/yomitan-dictionaries) | [Zip Yomitan](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13.zip) | Tần suất -> Thêm nguồn tần suất… |
| Tần suất | [BCCWJ SUW+LUW](https://github.com/Kuuuube/yomitan-dictionaries) | [Zip Yomitan](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/BCCWJ_SUW_LUW_combined.zip) | Tần suất -> Thêm nguồn tần suất… |


<details>
<summary><strong>Giấy phép JMnedict</strong></summary>

Sử dụng các tập từ tên riêng đi kèm được dẫn xuất từ [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (dự án JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Khắc phục sự cố

| Vấn đề                    | Giải pháp                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| "Không thể kết nối với Anki" | Khởi động Anki và đảm bảo AnkiConnect đã được cài đặt.                                  |
| "Không tìm thấy bộ thẻ"         | Chọn một bộ thẻ đã có trong Cài đặt -> Thẻ & Anki. Bộ thẻ không được tạo sẵn cho bạn; nếu cần bộ thẻ mới, hãy tạo trong Anki trước. |
| "Không tìm thấy loại ghi chú"    | Cấu hình tên các trường của loại ghi chú trong Cài đặt -> Thẻ & Anki.               |
| "Không tìm thấy ffmpeg"       | Cài đặt ffmpeg và thêm nó vào PATH.                                               |
| Không tìm thấy định nghĩa nào     | Thêm một từ điển Yomitan trong Cài đặt -> Thêm từ điển… (khuyến nghị), hoặc bật dự phòng Jisho (chậm hơn, bị giới hạn tốc độ). |
| Trình cài đặt Windows không mở được / cảnh báo SmartScreen | Xem [Lưu ý lần chạy đầu](#lưu-ý-lần-chạy-đầu-bản-dựng-chưa-ký): chọn **More info** -> **Run anyway**; khôi phục các cảnh báo nhầm của Defender từ **Protection history**. |
| Bản cài mới không có định nghĩa | Chạy Công cụ -> Trình hướng dẫn cài đặt hoặc Công cụ -> Tải tài nguyên được đề xuất. Nếu nhập thủ công, hãy giữ nguyên tệp ZIP Yomitan (đừng giải nén). |
| Thêm từ điển bị treo hoặc thất bại | Ghi lại giai đoạn cuối cùng bạn nhìn thấy và đính kèm nhật ký (xem "Nhật ký nằm ở đâu?" bên dưới). Kèm theo tên, nguồn và kích thước tệp ZIP từ điển trong báo cáo. |
| Nhật ký nằm ở đâu?      | Dùng Trợ giúp -> Mở thư mục nhật ký, hoặc mở `%USERPROFILE%\.anki_miner\anki_miner.log` trên Windows hoặc `~/.anki_miner/anki_miner.log` trên macOS/Linux. Nhật ký xoay vòng dùng hậu tố `.1` đến `.5`. |
| Báo cáo lỗi          | Trợ giúp -> Xuất chẩn đoán… sẽ ghi một tệp ZIP chứa nhật ký và thông tin hệ thống vào vị trí bạn chọn. Hãy xem lại nó trước khi tải lên vì nó chứa đường dẫn và tên tệp từ máy tính của bạn. Không có gì được tải lên tự động. |
| Nhật ký chẩn đoán chi tiết hơn | Đặt `ANKI_MINER_LOG_LEVEL=DEBUG` trước khi khởi động Anki Miner để ghi lại chi tiết của yt-dlp, urllib3 và fugashi bên thứ ba. Mặc định là `WARNING`; nhật ký của Anki Miner vẫn ở mức DEBUG. |
| Âm thanh sai ngôn ngữ  | Công cụ thử các bản âm thanh tiếng Nhật trước, rồi mới lùi về bản mặc định.      |
| Phụ đề không khớp tiếng    | Dùng điều khiển bù thời gian phụ đề trong giao diện (khoảng ±300 giây).                 |

## Lộ trình

Danh sách ý tưởng cho các phiên bản Anki Miner trong tương lai. Không theo thứ tự ưu tiên. Các yêu cầu tính năng được ưu tiên trước.
- Đề xuất một tính năng - [Mở một issue](https://github.com/0xzerolight/anki_miner/issues).
- Thảo luận về lộ trình - [Discussions](https://github.com/0xzerolight/anki_miner/discussions).

- **Tính năng**:
  - [x] Chọn ngôn ngữ giao diện.
  - [x] Tab tạo phụ đề cục bộ: tab tùy chọn để tạo phụ đề ngay trên máy.
  - [x] Tab Cách đọc: khai thác manga và sách.
  - [x] Công cụ điền bổ sung.
  - [ ] Thư viện media: mở rộng tab Phân tích để hiển thị thư viện media cục bộ trên mọi dạng media.
  - [ ] Tự động tải phụ đề.

- **Dài hạn**:
  - [x] Bản chuyển sang Android -- https://github.com/0xzerolight/anki_miner_android
  - [ ] Vượt ra ngoài tiếng Nhật: khai thác các ngôn ngữ khác.
  - [ ] Tiện ích mở rộng trình duyệt cho Anki Miner.


## Đóng góp

Mọi hình thức đóng góp đều được hoan nghênh.
Nếu bạn muốn ủng hộ dự án, hãy chia sẻ nó với những người khác có thể hưởng lợi từ nó.

- Mới đến đây? Hãy bắt đầu với [CONTRIBUTING.md](../CONTRIBUTING.md).
- Tổng quan kiến trúc: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Quy tắc ứng xử: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Bảo mật: [SECURITY.md](../SECURITY.md).

Báo lỗi và yêu cầu tính năng -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Câu hỏi chung và thảo luận -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) hoặc [Discord](https://discord.com/invite/aDtQyZzUVP).

## Lời cảm ơn đặc biệt

Chân thành cảm ơn những người đã có đóng góp xuất sắc cho dự án:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Những đề xuất tính năng tuyệt vời, kiểm thử bản phát hành mới, xây dựng cộng đồng.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Những đề xuất tính năng xuất sắc, xây dựng cộng đồng và điều hành trên Discord.

Xem [CONTRIBUTORS.md](../CONTRIBUTORS.md) để biết tất cả những ai đã đóng góp cho dự án dưới mọi hình thức.


## Giấy phép

GNU General Public License v3.0. Xem [LICENSE](../LICENSE).
