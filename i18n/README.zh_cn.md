<!-- i18n-source: README.md sha256:2694be0f35a3ecf6 -->

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
<b>简体中文</b> ·
<a href="README.zh_tw.md">繁體中文</a> ·
<a href="README.it.md">Italiano</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
把原汁原味的日语、中文和韩语内容变成 Anki 词汇卡片。
</p>

<p align="center">
Android 上也可用 - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner for Android</a>。
</p>

<p align="center">
如果 Anki Miner 帮到了你，请留下一颗 ⭐ star - 这能帮助更多人找到它 :)。
</p>


# <p align="center">挖词演示</p>

![Anki Miner Showcase](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">含声音的完整演示（MP4）</a></p>

### 卡片示例

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4（含声音）](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4（含声音）](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4（含声音）](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## 安装

### 系统要求

- **Anki**，并安装 [AnkiConnect](https://ankiweb.net/shared/info/2055492159) 插件（代码 `2055492159`）
- **ffmpeg** + **libmpv**（仅用于视频预览）- 只有通过 pip/pipx 或源码安装时才需要。

请从[最新发布版本](https://github.com/0xzerolight/anki_miner/releases/latest)下载适合你平台的安装包：

| 平台 | 下载 |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS（Apple Silicon / M1-M4） | `AnkiMiner-*-macOS-arm64.tar.gz` |
| macOS（Intel） | `AnkiMiner-*-macOS-x86_64.tar.gz` ¹ |
| Linux（Debian/Ubuntu） | `anki-miner_*_amd64.deb` |
| Linux（其他） | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ 不含本地 Whisper 字幕生成和 AVIF 截图。如需完整功能：`pipx install "anki-miner[asr]"`。

### 首次运行说明（未签名版本）

- **macOS**：Gatekeeper 会拦截本应用。请先解压，然后执行 `xattr -dr com.apple.quarantine AnkiMiner/`
- **Windows SmartScreen**：**更多信息** -> **仍要运行**。
- **Windows Defender 误报**：从**保护历史记录**中恢复，或[向 Microsoft 报告](https://www.microsoft.com/en-us/wdsi/filesubmission)。

<details>
<summary><strong>从 PyPI 安装（Python 3.11+）</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

日语无需额外安装。要挖取中文或韩文，请加装引擎:

```bash
pipx install "anki-miner[languages]"   # both; or [zh] / [ko] for one
```

上面的下载版会改为在应用内获取，位于设置 -> 挖词语言。

</details>

<details>
<summary><strong>从源码安装</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

完整的开发环境配置见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

</details>

## 标签页

- **视频** - 挖词单个视频/字幕配对、批量文件夹或 YouTube 链接。
- **牌组构建器** - 把整部剧集挖词成一个按词频排序的牌组。
- **有声书** - 挖词有声书、播客、广播和歌曲（音频 + 字幕/文稿配对）。
- **阅读** - 挖词漫画（mokuro）、小说（`.epub`、`.txt`；单本书或整个文件夹）、独立字幕文件，或粘贴的文本。
- **分析** - 挖词历史、难度排名、里程碑。
- **工具** - 生成字幕（本地 Whisper）、重新校准字幕时间轴（ffsubsync/alass）、把媒体压缩成纯对话音频、从 yt-dlp 支持的任意网站下载视频/音频/字幕、把现成牌组中值得学习的部分复制到新牌组，以及为已有卡片回填字段。
- **设置** - 所有可配置项。

## 其他功能

- 挖词语言 - 日语、中文和韩语，在设置中切换。韩语的语言模型在应用内下载。
- 单词整理器 - 在生成卡片之前逐一审阅每个候选单词，画面、漫画页面和词典条目并排显示。
- 撤销一次运行 - 直接在结果对话框中删除该次运行刚创建的笔记。
- 丰富的过滤器：i+1、词频排名区间、黑名单、正则表达式、词汇集等。
- 离线 Yomitan 词典导入 - 释义、音调、词频 - 按优先级串联。
- 多个词频列表按优先级串联。
- 卡片上的单词发音，来自本地音频包、JapanesePod101 或 Google TTS。
- 阅读卡片上的句子发音，来自 Google 翻译 TTS 或 Naver Papago（默认关闭）。
- 按词典分别设置释义样式，Yomitan 风格。
- 内嵌 libmpv 视频预览 - 整理单词时播放该词所在的画面，或一边实时播放一边微调字幕时间。
- 动画截图（见上方卡片示例）。
- 设置配置档 - 保存具名配置，并从顶栏切换。
- 重新设置挖词卡片样式 - 把当前的卡片样式重新应用到你已经生成的卡片（工具菜单）。

<details>
<summary><strong>内置主题（29 款）</strong></summary>

- **Ayu** - Light、Mirage、Dark
- **Catppuccin** - Latte（浅色）；Frappé、Macchiato、Mocha（深色）
- **Dracula** - Dracula、Alucard
- **Everforest** - Light、Dark
- **GitHub** - Light；Dark、Dark Dimmed
- **Gruvbox** - Light Medium、Dark Medium
- **Kanagawa** - Lotus（浅色）、Wave（深色）
- **Rosé Pine** - Dawn（浅色）；Main、Moon（深色）
- **Solarized** - Light、Dark
- **独立主题** - Light、Dark、Sakura、Nord、One Dark、Tokyo Night

主题许可证：[LICENSE-THEMES.md](../LICENSE-THEMES.md)。 
想加入其他主题？请在 GitHub Issue 中提出。

</details>

<details>
<summary><strong>工作原理</strong></summary>

1. **读取字幕**，并把文本切分成一个个单词。
2. **过滤**出你还不认识的实词 - 也可以在单词整理器里自己审阅这份列表。
3. 为每一句**从视频中抓取截图和音频片段**。
4. 在你配置的离线词典中**查找释义**，如果启用了，还可以回退到在线 Jisho（较慢，有速率限制）。
5. **把做好的卡片发送到 Anki。**

</details>

## 推荐资源

未特别标注者为日语。设置向导会按你的挖词语言推荐对应的组合。

| 类型 | 资源 | 下载 | 添加方式 |
|------|----------|----------|---------|
| 词典 | [JMdict](https://github.com/yomidevs/jmdict-yomitan) | [Yomitan zip](https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip) | 添加词典… |
| 词典 | [Jitendex](https://jitendex.org/) | [Yomitan zip](https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip) | 添加词典… |
| 词典 | [Bee's Character Dictionary](https://characterdictionary.tokyo/) | 在网站上生成 | 添加词典… |
| 音调 | [Kanjium](https://github.com/mifunetoshiro/kanjium) | [TSV](https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt) | 音调 -> 添加音调来源… |
| 音调 | [アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries) | [Drive](https://drive.google.com/drive/folders/1tTdLppnqMfVC5otPlX_cs4ixlIgjv_lH) | 音调 -> 添加音调来源… |
| 词频 | [JPDB v2.2 Kana](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13.zip) | 词频 -> 添加词频来源… |
| 词频 | [BCCWJ SUW+LUW](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/BCCWJ_SUW_LUW_combined.zip) | 词频 -> 添加词频来源… |
| 单词音频 | [local-audio-yomichan](https://github.com/yomidevs/local-audio-yomichan) | 合集种子或生成的 `android.db` | 音频 -> 添加音频来源… |
| 词典（中文） | [CC-CEDICT](https://github.com/MarvNC/cc-cedict-yomitan) | [Yomitan zip](https://github.com/MarvNC/cc-cedict-yomitan/releases/latest/download/CC-CEDICT.zip) | 添加词典… |
| 词典（韩文） | [KRDICT](https://github.com/Lyroxide/yomitan-ko-dic) | [Yomitan zip](https://github.com/Lyroxide/yomitan-ko-dic/releases/latest/download/KO-EN.KRDICT.No.Examples.zip) | 添加词典… |


<details>
<summary><strong>JMnedict 许可证</strong></summary>

使用了源自 [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) 的内置人名词汇集（JMdict/EDICT 项目，EDRDG，CC BY-SA 4.0）。

</details>

## 疑难解答

| 问题                    | 解决方法                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| “无法连接到 Anki” | 启动 Anki，并确认已安装 AnkiConnect。                                  |
| “未找到牌组”         | 在 设置 -> 卡片和 Anki 中选择一个已有的牌组。牌组不会自动创建；如果需要新牌组，请先在 Anki 中创建。 |
| “未找到笔记类型”    | 在 设置 -> 卡片和 Anki 中配置你的笔记类型字段名。               |
| “未找到 ffmpeg”       | 安装 ffmpeg 并把它加入 PATH。                                               |
| 找不到任何释义     | 在 设置 -> 添加词典… 中添加一个 Yomitan 词典（推荐），或启用 Jisho 回退（较慢，有速率限制）。 |
| Windows 安装程序打不开 / SmartScreen 警告 | 见[首次运行说明](#首次运行说明未签名版本)：选择**更多信息** -> **仍要运行**；Defender 误报可从**保护历史记录**中恢复。 |
| 全新安装没有任何释义 | 运行 工具 -> 设置向导 或 工具 -> 下载推荐资源。手动导入时请保持 Yomitan ZIP 原样（不要解压）。 |
| 添加词典卡住或失败 | 记下最后可见的阶段并附上日志（见下方“日志在哪里？”）。请在报告中写明词典 ZIP 的名称、来源和大小。 |
| 日志在哪里？      | 使用 帮助 -> 打开日志文件夹，或在 Windows 上打开 `%USERPROFILE%\.anki_miner\anki_miner.log`，在 macOS/Linux 上打开 `~/.anki_miner/anki_miner.log`。轮转日志使用 `.1` 到 `.5` 后缀。 如果有 `anki_miner.crash` 也请一并发送：让应用崩溃的故障会把调用栈写入该文件而非日志；保存辅助进程输出的 `anki_miner.child.log` 同样如此。 |
| 报告缺陷          | 帮助 -> 导出诊断信息… 会把日志（`anki_miner.log` 及其轮转文件、`anki_miner.crash`、`anki_miner.child.log`）、你的 `settings.json`、配置与界面状态文件、队列快照与下载清单，以及关于本机和应用状态的生成报告（`environment.txt`、`health.txt`、`resources.txt`、`stores.txt`、`disk.txt`、`screens.txt`）写入一个 ZIP，保存到你选择的位置。上传前请先检查内容，因为其中含有你电脑上的文件路径和文件名。不会自动上传任何内容。 |
| 更详细的诊断日志 | 启动 Anki Miner 前设置 `ANKI_MINER_LOG_LEVEL=DEBUG`，即可记录第三方 yt-dlp、urllib3 和 fugashi 的详细信息。默认为 `WARNING`；Anki Miner 自身的日志仍为 DEBUG。 |
| 音频语言不对  | 本工具会优先尝试挖词语言的音轨，然后回退到默认音轨。      |
| 字幕不同步    | 使用 GUI 中的字幕偏移控件（范围 ±300 秒）。                 |

## 路线图

Anki Miner 未来版本的想法清单。排列顺序不代表优先级。功能请求优先。
- 建议新功能 - [提交 issue](https://github.com/0xzerolight/anki_miner/issues)。
- 讨论路线图 - [Discussions](https://github.com/0xzerolight/anki_miner/discussions)。

- **功能**：
  - [x] 界面语言选择。
  - [x] 本地字幕生成标签页：可选启用的本地字幕生成标签页。
  - [x] 阅读标签页：挖词漫画和书籍。
  - [x] 回填工具。
  - [ ] 媒体库：扩展分析标签页，展示涵盖所有媒体形式的本地媒体库。
  - [ ] 自动下载字幕。

- **长期**：
  - [x] Android 移植 -- https://github.com/0xzerolight/anki_miner_android
  - [x] 超越日语：挖词中文和韩语。
  - [ ] Anki Miner 浏览器扩展。


## 参与贡献

欢迎任何形式的贡献。
如果你想支持这个项目，请把它分享给可能从中受益的人。

- 第一次来？请从 [CONTRIBUTING.md](../CONTRIBUTING.md) 开始。
- 架构概览：[ARCHITECTURE.md](../ARCHITECTURE.md)。
- 行为准则：[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)。
- 安全：[SECURITY.md](../SECURITY.md)。

缺陷报告和功能请求 -> [Issues](https://github.com/0xzerolight/anki_miner/issues)。
一般问题和讨论 -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) 或 [Discord](https://discord.com/invite/aDtQyZzUVP)。

## 特别感谢

衷心感谢为本项目做出杰出贡献的各位：

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - 出色的功能建议、新版本测试、社区建设。
- ★ **[rob-olvr](https://github.com/rob-olvr)** - 优秀的功能建议、社区建设以及 Discord 上的管理。

所有为本项目做出过任何贡献的人，见 [CONTRIBUTORS.md](../CONTRIBUTORS.md)。


## 许可证

GNU 通用公共许可证 v3.0。见 [LICENSE](../LICENSE)。
