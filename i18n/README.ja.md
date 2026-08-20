<!-- i18n-source: README.md sha256:5648ea423d8eee0f -->

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
<b>日本語</b> ·
<a href="README.ru.md">Русский</a> ·
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
日本語のネイティブコンテンツを Anki の語彙カードに変換します。
</p>

<p align="center">
Android にも対応 - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner for Android</a>。
</p>

<p align="center">
Anki Miner が役に立ったら ⭐ スターをお願いします - 他の人が見つけやすくなります :)。
</p>


# <p align="center">マイニングのデモ</p>

![Anki Miner Showcase](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">音声付きのフルデモ（MP4）</a></p>

### カードの例

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4（音声付き）](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4（音声付き）](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4（音声付き）](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## インストール

### 動作要件

- **Anki** と [AnkiConnect](https://ankiweb.net/shared/info/2055492159) アドオン（コード `2055492159`）
- **ffmpeg** + **libmpv**（動画プレビューのみ）- pip/pipx またはソースからインストールする場合にのみ必要です。

お使いのプラットフォーム向けのダウンロードは[最新リリース](https://github.com/0xzerolight/anki_miner/releases/latest)から入手してください:

| プラットフォーム | ダウンロード |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS（Apple Silicon / M1-M4） | `AnkiMiner-macOS-arm64.tar.gz` |
| macOS（Intel） | `AnkiMiner-macOS-x86_64.tar.gz` ¹ |
| Linux（Debian/Ubuntu） | `anki-miner_*_amd64.deb` |
| Linux（その他） | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ ローカル Whisper による字幕生成と AVIF スクリーンショットは含まれません。すべての機能を使うには: `pipx install "anki-miner[asr]"`。

### 初回起動時の注意（未署名ビルド）

- **macOS**: Gatekeeper がアプリをブロックします。先に展開してから `xattr -dr com.apple.quarantine AnkiMiner/`
- **Windows SmartScreen**: **詳細情報** -> **実行**。
- **Windows Defender の誤検知**: **保護の履歴** から復元するか、[Microsoft に報告](https://www.microsoft.com/en-us/wdsi/filesubmission)してください。

<details>
<summary><strong>PyPI からインストール（Python 3.11+）</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

</details>

<details>
<summary><strong>ソースからインストール</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

開発環境の完全なセットアップは [CONTRIBUTING.md](../CONTRIBUTING.md) を参照してください。

</details>

## タブ

- **動画** - 単一の動画/字幕のペア、バッチフォルダ、または YouTube の URL をマイニングします。
- **デッキビルダー** - シリーズ全体をマイニングして、頻度順に並んだ 1 つのデッキにまとめます。
- **オーディオブック** - オーディオブック、ポッドキャスト、ラジオ、楽曲（音声 + 字幕/文字起こしのペア）をマイニングします。
- **読み** - 漫画（mokuro）、小説（`.epub`、`.txt`。1 冊でもフォルダ全体でも可）、単体の字幕ファイル、貼り付けた日本語テキストをマイニングします。
- **分析** - マイニング履歴、難易度ランキング、マイルストーン。
- **ユーティリティ** - 字幕の生成（ローカル Whisper）、字幕のタイミング調整（ffsubsync/alass）、メディアをセリフだけの音声に凝縮、既製デッキの学ぶ価値がある部分を新しいデッキにコピー、既存カードのフィールドの補完。
- **設定** - 設定できるものすべて。

## その他の機能

- 単語キュレーター - カードが作られる前に候補の単語をすべて確認できます。シーン、漫画のページ、辞書項目を並べて表示します。
- 実行の取り消し - 実行が作成したばかりのノートを、結果ダイアログからそのまま削除できます。
- 豊富なフィルタリング: i+1、頻度ランクの範囲、ブラックリスト、正規表現、単語セットなど。
- オフラインの Yomitan 辞書のインポート - 語義、ピッチアクセント、頻度 - 優先順位で連鎖します。
- 複数の頻度リストを優先順位で連鎖できます。
- ローカルの音声パック、JapanesePod101、Google TTS からカードに単語の音声を付けられます。
- 「読み」のカードには Google 翻訳 TTS または Naver Papago の例文音声を付けられます（既定ではオフ）。
- 辞書ごとの語義スタイル設定（Yomitan 風）。
- 埋め込み libmpv による動画プレビュー - キュレーション中に単語のシーンを再生したり、再生を見ながら字幕のタイミングを微調整したりできます。
- アニメーションするスクリーンショット（上のカードの例を参照）。
- 設定プロファイル - 名前を付けた設定を保存し、ヘッダーから切り替えられます。
- マイニングしたカードのスタイルを再適用 - 現在のカードスタイルを、すでに作成済みのカードに適用し直します（ツールメニュー）。

<details>
<summary><strong>組み込みテーマ（29 種類）</strong></summary>

- **Ayu** - Light、Mirage、Dark
- **Catppuccin** - Latte（ライト）; Frappé、Macchiato、Mocha（ダーク）
- **Dracula** - Dracula、Alucard
- **Everforest** - Light、Dark
- **GitHub** - Light; Dark、Dark Dimmed
- **Gruvbox** - Light Medium、Dark Medium
- **Kanagawa** - Lotus（ライト）、Wave（ダーク）
- **Rosé Pine** - Dawn（ライト）; Main、Moon（ダーク）
- **Solarized** - Light、Dark
- **Standalone** - Light、Dark、Sakura、Nord、One Dark、Tokyo Night

テーマのライセンス: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
別のテーマを追加してほしいですか？ GitHub Issue で提案してください。

</details>

<details>
<summary><strong>仕組み</strong></summary>

1. **字幕を読み込み**、日本語を個々の単語に分割します。
2. まだ知らない内容語だけに**フィルタリング**します。必要なら単語キュレーターで自分でリストを確認できます。
3. 各行について、動画から**スクリーンショットと音声クリップを取得**します。
4. 設定したオフライン辞書で**語義を検索**します。有効にしていればオンラインの Jisho にフォールバックすることもできます（低速、レート制限あり）。
5. **完成したカードを Anki に送信します。**

</details>

## 推奨リソース

| 種類 | リソース | ダウンロード | 追加方法 |
|------|----------|----------|---------|
| 辞書 | [JMdict](https://github.com/yomidevs/jmdict-yomitan) | [Yomitan zip](https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip) | 辞書を追加… |
| 辞書 | [Jitendex](https://jitendex.org/) | [Yomitan zip](https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip) | 辞書を追加… |
| 辞書 | [Bee's Character Dictionary](https://characterdictionary.tokyo/) | サイト上で生成 | 辞書を追加… |
| ピッチ | [Kanjium](https://github.com/mifunetoshiro/kanjium) | [TSV](https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt) | ピッチアクセント -> ピッチソースを追加… |
| ピッチ | [アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries) | [Drive](https://drive.google.com/drive/folders/1tTdLppnqMfVC5otPlX_cs4ixlIgjv_lH) | ピッチアクセント -> ピッチソースを追加… |
| 頻度 | [JPDB v2.2 Kana](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13.zip) | 頻度 -> 頻度ソースを追加… |
| 頻度 | [BCCWJ SUW+LUW](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/BCCWJ_SUW_LUW_combined.zip) | 頻度 -> 頻度ソースを追加… |
| 単語音声 | [local-audio-yomichan](https://github.com/yomidevs/local-audio-yomichan) | コレクションの torrent または生成した `android.db` | 音声 -> 音声ソースを追加… |


<details>
<summary><strong>JMnedict のライセンス</strong></summary>

[JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html)（JMdict/EDICT プロジェクト、EDRDG、CC BY-SA 4.0）から派生した人名の単語セットを同梱して使用しています。

</details>

## トラブルシューティング

| 問題                    | 解決方法                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| 「Anki に接続できません」 | Anki を起動し、AnkiConnect がインストールされていることを確認してください。       |
| 「デッキが見つかりません」 | 設定 -> カードと Anki で既存のデッキを選んでください。デッキは自動では作成されません。新しいデッキが必要な場合は、先に Anki で作成してください。 |
| 「ノートタイプが見つかりません」 | 設定 -> カードと Anki でノートタイプのフィールド名を設定してください。            |
| 「ffmpeg が見つかりません」 | ffmpeg をインストールし、PATH に追加してください。                               |
| 語義が見つからない       | 設定 -> 辞書を追加… で Yomitan 辞書を追加するか（推奨）、Jisho へのフォールバックを有効にしてください（低速、レート制限あり）。 |
| Windows のインストーラーが開かない / SmartScreen の警告 | [初回起動時の注意](#初回起動時の注意未署名ビルド)を参照してください: **詳細情報** -> **実行** を選びます。Defender の誤検知は **保護の履歴** から復元してください。 |
| 新規インストールで語義が出ない | ツール -> セットアップウィザード、またはツール -> 推奨リソースをダウンロード を実行してください。手動でインポートする場合は、Yomitan の ZIP をそのままの状態にしておいてください（解凍しないでください）。 |
| 辞書を追加 が止まる、または失敗する | 最後に見えた段階を控え、ログを添付してください（下の「ログはどこにありますか？」を参照）。報告には辞書 ZIP の名前、入手元、サイズを含めてください。 |
| ログはどこにありますか？ | ヘルプ -> ログフォルダを開く を使うか、Windows では `%USERPROFILE%\.anki_miner\anki_miner.log`、macOS/Linux では `~/.anki_miner/anki_miner.log` を開いてください。ローテーションされたログには `.1` から `.5` の接尾辞が付きます。 |
| バグを報告する           | ヘルプ -> 診断情報をエクスポート… で、ログとシステム情報を含む ZIP を任意の場所に書き出します。お使いのコンピューターのファイルパスやファイル名が含まれるため、アップロードする前に内容を確認してください。自動でアップロードされるものはありません。 |
| 診断ログを増やしたい | Anki Miner を起動する前に `ANKI_MINER_LOG_LEVEL=DEBUG` を設定すると、サードパーティの yt-dlp、urllib3、fugashi の詳細を記録できます。既定は `WARNING` で、Anki Miner 自身のログは DEBUG のままです。 |
| 音声の言語が違う         | 最初に日本語の音声トラックを試し、なければ既定のものにフォールバックします。      |
| 字幕がずれている         | GUI の字幕オフセット調整を使ってください（範囲は ±300 秒）。                     |

## ロードマップ

Anki Miner の今後のバージョンに向けたアイデアの一覧です。優先順ではありません。機能リクエストが優先されます。
- 機能を提案する - [Issue を作成](https://github.com/0xzerolight/anki_miner/issues)。
- ロードマップについて議論する - [Discussions](https://github.com/0xzerolight/anki_miner/discussions)。

- **機能**:
  - [x] UI の言語選択。
  - [x] ローカル字幕作成タブ: ローカルで字幕を生成するオプトインのタブ。
  - [x] 読みタブ: 漫画と書籍のマイニング。
  - [x] 補完ツール。
  - [ ] メディアライブラリ: 分析タブを拡張し、あらゆる形式のローカルメディアライブラリを表示する。
  - [ ] 字幕の自動ダウンロード。

- **長期**:
  - [x] Android への移植 -- https://github.com/0xzerolight/anki_miner_android
  - [ ] 日本語の先へ: 他の言語のマイニング。
  - [ ] Anki Miner のブラウザ拡張機能。


## 貢献

どのような形の貢献も歓迎します。
プロジェクトを応援したい方は、役に立ちそうな人に共有してください。

- はじめての方は [CONTRIBUTING.md](../CONTRIBUTING.md) から。
- アーキテクチャの概要: [ARCHITECTURE.md](../ARCHITECTURE.md)。
- 行動規範: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)。
- セキュリティ: [SECURITY.md](../SECURITY.md)。

バグ報告と機能リクエスト -> [Issues](https://github.com/0xzerolight/anki_miner/issues)。
一般的な質問や議論 -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) または [Discord](https://discord.com/invite/aDtQyZzUVP)。

## 特別な感謝

プロジェクトに格別な貢献をしてくださった方々に心から感謝します:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - 素晴らしい機能の提案、新リリースのテスト、コミュニティづくり。
- ★ **[rob-olvr](https://github.com/rob-olvr)** - 優れた機能の提案、Discord でのコミュニティづくりとモデレーション。

プロジェクトに何らかの貢献をしてくださったすべての方は [CONTRIBUTORS.md](../CONTRIBUTORS.md) をご覧ください。


## ライセンス

GNU General Public License v3.0。[LICENSE](../LICENSE) を参照してください。
