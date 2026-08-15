<!-- i18n-source: README.md sha256:edbfeed099296e19 -->

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
<b>繁體中文</b> ·
<a href="README.it.md">Italiano</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
把日文原生內容變成 Anki 單字卡片。
</p>

<p align="center">
Android 上也能用 - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner for Android</a>。
</p>

<p align="center">
如果 Anki Miner 對你有幫助，請留下一顆 ⭐ 星星 - 這能讓更多人找到它 :)。
</p>


# <p align="center">採集示範</p>

![Anki Miner Showcase](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">含聲音的完整示範（MP4）</a></p>

### 卡片範例

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4（含聲音）](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4（含聲音）](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4（含聲音）](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## 安裝

### 需求

- **Anki** 並安裝 [AnkiConnect](https://ankiweb.net/shared/info/2055492159) 附加元件（代碼 `2055492159`）
- **ffmpeg** + **libmpv**（僅影片預覽需要） - 只有透過 pip/pipx 或原始碼安裝時才需要。

請從[最新發行版本](https://github.com/0xzerolight/anki_miner/releases/latest)下載適用你平台的檔案：

| 平台 | 下載檔案 |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS（Apple Silicon / M1-M4） | `AnkiMiner-macOS-arm64.tar.gz` |
| macOS（Intel） | `AnkiMiner-macOS-x86_64.tar.gz` ¹ |
| Linux（Debian/Ubuntu） | `anki-miner_*_amd64.deb` |
| Linux（其他） | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ 不含本機 Whisper 字幕生成與 AVIF 螢幕截圖。若要完整功能：`pipx install "anki-miner[asr]"`。

### 首次執行注意事項（未簽章版本）

- **macOS**：Gatekeeper 會封鎖此應用程式。請先解壓縮，再執行 `xattr -dr com.apple.quarantine AnkiMiner/`
- **Windows SmartScreen**：**其他資訊** -> **仍要執行**。
- **Windows Defender 誤判**：從**保護歷程記錄**還原，或[回報給 Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission)。

<details>
<summary><strong>從 PyPI 安裝（Python 3.11+）</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

</details>

<details>
<summary><strong>從原始碼安裝</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

完整的開發環境設定請見 [CONTRIBUTING.md](../CONTRIBUTING.md)。

</details>

## 分頁

- **影片** - 採集單一影片／字幕組合、批次資料夾，或 YouTube 網址。
- **牌組建立器** - 把整部影集採集成一個依頻率排序的牌組。
- **有聲書** - 採集有聲書、Podcast、廣播、歌曲（音訊 + 字幕／逐字稿組合）。
- **閱讀** - 採集漫畫（mokuro）、小說（`.epub`、`.txt`；單本書或整個資料夾）、獨立字幕檔，或貼上的日文文字。
- **分析** - 採集歷史、難度排名、里程碑、復原。
- **工具** - 生成字幕（本機 Whisper）、重新校時字幕（alass）、把媒體濃縮成只有對話的音訊、把現成牌組中值得學的部分複製到新牌組，以及為既有卡片補齊欄位。
- **設定** - 所有可調整的項目。

## 其他功能

- 單字整理工具 - 在製作卡片前逐一檢視每個候選單字，並排顯示它的場景、漫畫頁面與字典條目。
- 完整的篩選機制：i+1、頻率排名區間、黑名單、正規表示式、單字集等等。
- 離線 Yomitan 字典匯入 - 釋義、高低音調、頻率 - 依優先順序串接。
- 多份頻率清單依優先順序串接。
- 卡片上的單字音訊，來源可為本機音訊包、JapanesePod101 或 Google TTS。
- 閱讀卡片上的句子音訊，來源可為 Google Translate TTS 或 Naver Papago（預設關閉）。
- 各字典獨立的釋義樣式，Yomitan 風格。
- 內嵌的 libmpv 影片預覽 - 整理單字時播放該單字的場景，或在即時播放中微調字幕時間。
- 動態螢幕截圖（見上方的卡片範例）。
- 設定檔 - 儲存具名設定組合，並從標題列切換。
- 重新設定採集卡片樣式 - 把你目前的卡片樣式重新套用到已製作的卡片上（工具選單）。

<details>
<summary><strong>內建主題（29 種）</strong></summary>

- **Ayu** - Light、Mirage、Dark
- **Catppuccin** - Latte（淺色）；Frappé、Macchiato、Mocha（深色）
- **Dracula** - Dracula、Alucard
- **Everforest** - Light、Dark
- **GitHub** - Light；Dark、Dark Dimmed
- **Gruvbox** - Light Medium、Dark Medium
- **Kanagawa** - Lotus（淺色）、Wave（深色）
- **Rosé Pine** - Dawn（淺色）；Main、Moon（深色）
- **Solarized** - Light、Dark
- **Standalone** - Light、Dark、Sakura、Nord、One Dark、Tokyo Night

主題授權：[LICENSE-THEMES.md](../LICENSE-THEMES.md)。 
想新增其他主題？請在 GitHub Issue 中提出建議。

</details>

<details>
<summary><strong>運作方式</strong></summary>

1. **讀取字幕**並把日文切分成一個個單字。
2. **篩選**出你還不認識的實詞 - 也可以自己在單字整理工具中檢視清單。
3. 為每一行**擷取螢幕截圖與音訊片段**。
4. 在你設定的離線字典中**查詢釋義**，若有啟用則可退回線上 Jisho（較慢，有速率限制）。
5. **把完成的卡片送到 Anki。**

</details>

## 推薦資源

| 類型 | 資源 | 下載 | 加入方式 |
|------|----------|----------|---------|
| 字典 | [JMdict](https://github.com/yomidevs/jmdict-yomitan) | [Yomitan zip](https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip) | 新增字典… |
| 字典 | [Jitendex](https://jitendex.org/) | [Yomitan zip](https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip) | 新增字典… |
| 字典 | [Bee's Character Dictionary](https://characterdictionary.tokyo/) | 於網站上產生 | 新增字典… |
| 高低音調 | [Kanjium](https://github.com/mifunetoshiro/kanjium) | [TSV](https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt) | 高低音調 -> 新增高低音調來源… |
| 高低音調 | [アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries) | [Drive](https://drive.google.com/drive/folders/1tTdLppnqMfVC5otPlX_cs4ixlIgjv_lH) | 高低音調 -> 新增高低音調來源… |
| 頻率 | [JPDB v2.2 Kana](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13.zip) | 頻率 -> 新增頻率來源… |
| 頻率 | [BCCWJ SUW+LUW](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/BCCWJ_SUW_LUW_combined.zip) | 頻率 -> 新增頻率來源… |


<details>
<summary><strong>JMnedict 授權</strong></summary>

使用衍生自 [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) 的內建人名單字集（JMdict/EDICT 專案，EDRDG，CC BY-SA 4.0）。

</details>

## 疑難排解

| 問題                    | 解決方式                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| 「無法連線至 Anki」 | 啟動 Anki，並確認已安裝 AnkiConnect。                                  |
| 「找不到牌組」         | 在設定 -> 卡片與 Anki 中選擇既有的牌組。程式不會替你建立牌組；若需要新牌組，請先在 Anki 中建立。 |
| 「找不到筆記類型」    | 在設定 -> 卡片與 Anki 中設定你的筆記類型欄位名稱。               |
| 「找不到 ffmpeg」       | 安裝 ffmpeg 並把它加入 PATH。                                               |
| 找不到任何釋義     | 在設定 -> 新增字典… 中加入 Yomitan 字典（建議做法），或啟用 Jisho 備援（較慢，有速率限制）。 |
| Windows 安裝程式無法開啟／出現 SmartScreen 警告 | 見[首次執行注意事項](#首次執行注意事項未簽章版本)：選擇**其他資訊** -> **仍要執行**；Defender 誤判請從**保護歷程記錄**還原。 |
| 全新安裝後沒有任何釋義 | 執行工具 -> 設定精靈，或工具 -> 下載推薦資源。若要手動匯入，請保持 Yomitan ZIP 原樣（不要解壓縮）。 |
| 新增字典卡住或失敗 | 記下最後看到的階段並附上記錄檔（見下方「記錄檔在哪裡？」）。回報時請附上字典 ZIP 的檔名、來源與大小。 |
| 記錄檔在哪裡？      | 使用說明 -> 開啟記錄資料夾，或在 Windows 上開啟 `%USERPROFILE%\.anki_miner\anki_miner.log`，macOS/Linux 上開啟 `~/.anki_miner/anki_miner.log`。輪替後的記錄檔使用 `.1` 到 `.5` 的後綴。 |
| 回報錯誤          | 說明 -> 匯出診斷資訊… 會把記錄檔與系統資訊寫成 ZIP，存到你選擇的位置。上傳前請先檢視內容，因為其中包含你電腦上的檔案路徑與檔名。程式不會自動上傳任何東西。 |
| 更詳細的診斷記錄 | 啟動 Anki Miner 前設定 `ANKI_MINER_LOG_LEVEL=DEBUG`，以擷取第三方 yt-dlp、urllib3 與 fugashi 的細節。預設為 `WARNING`；Anki Miner 本身的記錄維持在 DEBUG。 |
| 音訊語言不對  | 程式會先嘗試日文音軌，找不到才退回預設音軌。      |
| 字幕不同步    | 使用 GUI 中的字幕位移控制項（範圍 ±300 秒）。                 |

## 藍圖

Anki Miner 未來版本的構想清單。順序不代表優先度。功能請求優先。
- 建議功能 - [開啟 issue](https://github.com/0xzerolight/anki_miner/issues)。
- 討論藍圖 - [Discussions](https://github.com/0xzerolight/anki_miner/discussions)。

- **功能**：
  - [x] UI 語言選擇。
  - [x] 本機字幕製作分頁：可選用的分頁，在本機生成字幕。
  - [x] 閱讀分頁：採集漫畫與書籍。
  - [x] 補齊工具。
  - [ ] 媒體庫：擴充分析分頁，顯示涵蓋所有媒體形式的本機媒體庫。
  - [ ] 自動下載字幕。

- **長期目標**：
  - [x] Android 移植 -- https://github.com/0xzerolight/anki_miner_android
  - [ ] 超越日文：採集其他語言。
  - [ ] Anki Miner 瀏覽器擴充功能。


## 參與貢獻

歡迎任何形式的貢獻。
如果你想支持這個專案，請把它分享給可能用得上的人。

- 第一次來？請從 [CONTRIBUTING.md](../CONTRIBUTING.md) 開始。
- 架構概覽：[ARCHITECTURE.md](../ARCHITECTURE.md)。
- 測試策略：[TESTING.md](../TESTING.md)。
- 行為準則：[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)。
- 安全性：[SECURITY.md](../SECURITY.md)。

錯誤回報與功能請求 -> [Issues](https://github.com/0xzerolight/anki_miner/issues)。
一般問題與討論 -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) 或 [Discord](https://discord.com/invite/aDtQyZzUVP)。

## 特別感謝

誠摯感謝為本專案做出卓越貢獻的人：

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - 出色的功能建議、新版本測試、社群經營。
- ★ **[rob-olvr](https://github.com/rob-olvr)** - 優秀的功能建議、Discord 上的社群經營與管理。

所有曾以任何形式貢獻本專案的人請見 [CONTRIBUTORS.md](../CONTRIBUTORS.md)。


## 授權

GNU General Public License v3.0。見 [LICENSE](../LICENSE)。
