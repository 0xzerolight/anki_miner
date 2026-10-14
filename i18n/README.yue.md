<!-- i18n-source: README.md sha256:0cda27a1b4d4fba9 -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="PyPI 版本"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="授權：GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="GitHub 下載次數"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="GitHub 星星"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord 社群"></a>
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
<b>粵語</b> ·
<a href="README.sl.md">Slovenščina</a> ·
<a href="README.ko.md">한국어</a> ·
<a href="README.pt_pt.md">Português (Portugal)</a> ·
<a href="README.nl.md">Nederlands</a> ·
<a href="README.th.md">ไทย</a> ·
<a href="README.he.md">עברית</a> ·
<a href="README.fa.md">فارسی</a> ·
<a href="README.ca.md">Català</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
將日文、中文、韓文同另外二十九種語言嘅原生內容，變成 Anki 詞彙卡片。
</p>

<p align="center">
Android 都有 - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner for Android</a>。
</p>

<p align="center">
如果 Anki Miner 幫到你，請畀粒 ⭐ 星 - 咁可以幫其他人搵到佢 :)。
</p>


# <p align="center">採集示範</p>

![Anki Miner 示範](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">有聲完整示範（MP4）</a></p>

### 卡片例子

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4（有聲）](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4（有聲）](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4（有聲）](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## 安裝

### 要求

- **Anki**，加埋 [AnkiConnect](https://ankiweb.net/shared/info/2055492159) 附加元件（代碼 `2055492159`）
- **ffmpeg** + **libmpv**（只係影片預覽用）- 只有用 pip/pipx 或者原始碼安裝先需要。

喺[最新版本](https://github.com/0xzerolight/anki_miner/releases/latest)下載適合你平台嘅檔案：

| 平台 | 下載 |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux（其他） | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ 唔包括 AVIF 截圖，同埋生成字幕嘅靜音移除。

### 第一次執行須知（未簽署版本）

- **macOS**：打開 `.dmg`，將 **AnkiMiner** 拖去 **Applications**，然後啟動佢。因為程式未經公證，第一次啟動會被阻擋：關閉對話框，再打開程式一次，然後揀 **Open Anyway**（亦可以喺 **System Settings** -> **Privacy & Security** 搵到）。只需要做一次。
- **Linux AppImage**：執行之前要設定佢可以執行 - `chmod +x AnkiMiner-*.AppImage`，或者喺檔案管理員用 **Properties** -> **Allow executing**。
- **Windows SmartScreen**：**More info** -> **Run anyway**。
- **Windows Defender 誤報**：喺 **Protection history** 還原，或者[向 Microsoft 報告](https://www.microsoft.com/en-us/wdsi/filesubmission)。

<details>
<summary><strong>由 PyPI 安裝（Python 3.11+）</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

日文、印尼文同希伯來文唔使額外安裝任何嘢。其他採集語言就要加埋佢嘅引擎：

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

阿拉伯文同波斯文冇額外套件。佢哋嘅資料，同埋歐洲語言嘅 spaCy 模型，都喺程式入面嘅「設定 -> 採集語言」下載；上面嘅下載版本會喺嗰度攞齊所有嘢。

</details>

<details>
<summary><strong>由原始碼安裝</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

完整開發環境設定請睇 [CONTRIBUTING.md](../CONTRIBUTING.md)。

</details>

## 分頁

- **影片** - 採集單一影片/字幕配對、一個批次資料夾、YouTube 或者 Bilibili 網址，或者將成套劇集整成一個按詞頻排序嘅牌組。
- **有聲書** - 採集有聲書、podcast、電台、歌曲（音訊 + 字幕/文字稿配對）。
- **閱讀** - 採集漫畫（mokuro）、小說（`.epub`、`.txt`；單本書或者成個資料夾）、獨立字幕檔案，或者貼上嘅文字。
- **分析** - 採集紀錄、難度排名、里程碑。
- **實用工具** - 生成字幕（本機 Whisper）、字幕對時（ffsubsync/alass）、將媒體濃縮成只有對白嘅音訊、由 yt-dlp 支援嘅任何網站下載影片/音訊/字幕、將現成牌組入面值得學嘅部分複製去新牌組、幫現有卡片補填欄位、將漫畫頁面圖片 OCR 成 .mokuro 檔案（mokuro），同埋將有聲書同本書嘅文字對時（有聲書同步）。
- **設定** - 所有可以設定嘅嘢。

## 其他功能

- 採集語言 - 日文、中文、韓文、英文、加泰隆尼亞文、德文、葡萄牙文、法文、西班牙文、意大利文、荷蘭文、挪威書面語、羅馬尼亞文、希臘文、芬蘭文、匈牙利文、克羅地亞文、瑞典文、波蘭文、立陶宛文、丹麥文、土耳其文、印尼文、俄文、阿拉伯文、泰文、波斯文、斯洛文尼亞文、烏克蘭文、越南文、粵語同希伯來文，喺設定入面切換。日文以外嘅語言會喺程式入面下載佢哋嘅引擎；印尼文同希伯來文唔使。
- 詞語整理器 - 建立卡片之前審閱每個候選詞語，旁邊同時顯示佢嘅場景、漫畫頁面同字典詞條。
- 復原一次執行 - 直接喺結果對話框刪除嗰次執行啱啱建立嘅筆記。
- 大量篩選選項：i+1、詞頻排名範圍、黑名單、正規表示式、詞集等等。
- 離線匯入 Yomitan 字典 - 釋義、高低音調、詞頻 - 按優先次序串連。
- 多個詞頻清單按優先次序串連。
- 卡片上嘅詞語發音可以來自本機音訊包、JapanesePod101、Google TTS 或者 Microsoft Edge TTS。
- 閱讀卡片嘅例句音訊來自 Google 翻譯 TTS，日文同韓文亦可以用 Naver Papago（預設關閉）。
- 每部字典獨立嘅釋義樣式，Yomitan 風格。
- 內嵌 libmpv 影片預覽 - 整理詞語時播放詞語嘅場景，或者一邊即時播放一邊微調字幕時間。
- 動態截圖（睇上面嘅卡片例子）。
- 設定檔 - 儲存有名嘅設定組合，喺頂部隨時切換。
- 重新套用卡片樣式 - 將目前嘅卡片樣式重新套用到你已經整好嘅卡片（實用工具 → 卡片補填）。

<details>
<summary><strong>內建主題（29 個）</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte（淺色）；Frappé、Macchiato、Mocha（深色）
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus（淺色）、Wave（深色）
- **Rosé Pine** - Dawn（淺色）；Main、Moon（深色）
- **Solarized** - Light, Dark
- **Standalone** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

主題授權：[LICENSE-THEMES.md](../LICENSE-THEMES.md)。 
想加多個主題？喺 GitHub Issue 提出建議。

</details>

<details>
<summary><strong>運作原理</strong></summary>

1. **讀取字幕**，將文字拆成一個個詞語。
2. **篩選**出你未識嘅實詞 - 可以選擇喺詞語整理器自己審閱清單。
3. 由影片為每行**擷取截圖同音訊片段**。
4. 喺你設定好嘅離線字典**查釋義**。
5. **將整好嘅卡片傳送去 Anki。**

</details>

## 建議資源

每個採集語言嘅字典同詞頻清單，連下載連結：[RESOURCES.md](../RESOURCES.md)。設定精靈會按你嘅採集語言提供合適嘅一套。

<details>
<summary><strong>JMnedict 授權</strong></summary>

使用由 [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html)（JMdict/EDICT 計劃，EDRDG，CC BY-SA 4.0）衍生嘅內建名字詞集。

</details>

## 疑難排解

| 問題                    | 解決方法                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| 「連唔到 Anki」 | 確認 Anki 開緊，同埋已經安裝 AnkiConnect。                                  |
| 「搵唔到牌組」         | 喺「設定 -> 卡片同 Anki」揀一個現有牌組。程式唔會幫你建立牌組；如果需要新牌組，請先喺 Anki 整好。 |
| 「搵唔到筆記類型」    | 喺「設定 -> 卡片同 Anki」設定你筆記類型嘅欄位名稱。               |
| 「搵唔到 ffmpeg」       | 安裝 ffmpeg 並加入 PATH。                                               |
| 搵唔到釋義     | 喺「設定 -> 字典 -> 加入字典…」加入一部 Yomitan 字典。 |
| Windows 安裝程式打唔開 / SmartScreen 警告 | 睇[第一次執行須知](#第一次執行須知未簽署版本)：揀 **More info** -> **Run anyway**；Defender 誤報喺 **Protection history** 還原。 |
| 新安裝冇釋義 | 執行「工具 -> 設定精靈」或者「工具 -> 下載建議資源」。手動匯入嘅話，保持 Yomitan ZIP 完整（唔好解壓）。 |
| 加入字典卡住或者失敗 | 記低最後見到嘅階段，並附上日誌（睇下面「日誌喺邊？」）。報告入面要寫埋字典 ZIP 嘅名、來源同大小。 |
| 日誌喺邊？      | 用「說明 -> 打開日誌資料夾」，或者喺 Windows 打開 `%USERPROFILE%\.anki_miner\anki_miner.log`、喺 macOS/Linux 打開 `~/.anki_miner/anki_miner.log`。輪替咗嘅日誌會用 `.1` 至 `.5` 後綴。如果有 `anki_miner.crash` 都請一齊傳送（令程式當機嘅崩潰會將堆疊寫入嗰個檔案，唔係寫入日誌），仲有 `anki_miner.child.log`，入面係輔助程序嘅輸出。 |
| 報告錯誤          | 「說明 -> 匯出診斷資料…」會將一個 ZIP 寫去你揀嘅位置，入面有日誌（`anki_miner.log` 同佢嘅輪替檔、`anki_miner.crash`、`anki_miner.child.log`）、你嘅 `settings.json`、設定同介面狀態檔案、隊列快照同下載清單，以及生成嘅電腦同程式狀態報告（`environment.txt`、`health.txt`、`resources.txt`、`stores.txt`、`disk.txt`、`screens.txt`）。上載之前請先睇一次，因為入面有你電腦上嘅檔案路徑同檔案名稱。乜嘢都唔會自動上載。 |
| 更多診斷日誌 | 啟動 Anki Miner 之前設定 `ANKI_MINER_LOG_LEVEL=DEBUG`，就可以記錄第三方 yt-dlp、urllib3 同 fugashi 嘅詳情。預設係 `WARNING`；Anki Miner 自己嘅日誌仍然係 DEBUG。 |
| 音訊語言唔啱  | 程式會揀採集語言嘅音訊軌道，冇就用第一條。你可以用「音訊軌道…」（影片 -> 單集）自己揀。 |
| 字幕唔同步    | 用介面入面嘅字幕偏移控制（範圍 ±300 秒）。                 |

## 參與貢獻

歡迎任何形式嘅貢獻。
如果你想支持呢個項目，請分享畀可能用得着嘅人。

- 新嚟？由 [CONTRIBUTING.md](../CONTRIBUTING.md) 開始。
- 架構概覽：[ARCHITECTURE.md](../ARCHITECTURE.md)。
- 行為守則：[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)。
- 安全：[SECURITY.md](../SECURITY.md)。

錯誤報告同功能建議 -> [Issues](https://github.com/0xzerolight/anki_miner/issues)。
一般問題同討論 -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) 或者 [Discord](https://discord.com/invite/aDtQyZzUVP)。

## 特別鳴謝

衷心多謝為呢個項目作出非凡貢獻嘅人：

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - 出色嘅功能建議、新版本測試、建立社群。
- ★ **[rob-olvr](https://github.com/rob-olvr)** - 優秀嘅功能建議、喺 Discord 建立同管理社群。

所有曾經以任何形式貢獻過呢個項目嘅人，請睇 [CONTRIBUTORS.md](../CONTRIBUTORS.md)。


## 授權

GNU General Public License v3.0。請睇 [LICENSE](../LICENSE)。
