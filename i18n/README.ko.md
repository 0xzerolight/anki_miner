<!-- i18n-source: README.md sha256:6f9163baa7a87e06 -->

<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="PyPI 버전"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="라이선스: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="GitHub 다운로드 수"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="GitHub 별표"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord 커뮤니티"></a>
</p>

<!-- i18n-nav:start -->
<div align="center">
<details>
<summary>언어: 한국어</summary>
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
<b>한국어</b> ·
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

<p align="center">
일본어, 중국어, 한국어를 비롯한 32개 언어의 원어 콘텐츠를 Anki 어휘 카드로 만듭니다.
</p>

<p align="center">
Android에서도 사용할 수 있습니다 - <a href="https://github.com/0xzerolight/anki_miner_android">Android용 Anki Miner</a>.
</p>

<p align="center">
Anki Miner가 도움이 되었다면 ⭐ 별표를 남겨 주세요. 다른 사람들이 찾는 데 도움이 됩니다 :).
</p>


# <p align="center">마이닝 데모</p>

![Anki Miner 소개](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">소리가 포함된 전체 데모(MP4)</a></p>

### 카드 예시

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4(소리 포함)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4(소리 포함)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4(소리 포함)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## 설치

### 요구 사항

- [AnkiConnect](https://ankiweb.net/shared/info/2055492159) 애드온(코드 `2055492159`)이 설치된 **Anki**
- **ffmpeg** + **libmpv**(동영상 미리 보기 전용) - pip/pipx 또는 소스로 설치할 때만 필요합니다.

[최신 릴리스](https://github.com/0xzerolight/anki_miner/releases/latest)에서 사용 중인 플랫폼에 맞는 파일을 다운로드하세요.

| 플랫폼 | 다운로드 |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (기타) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ AVIF 스크린샷과 생성된 자막의 무음 제거는 지원하지 않습니다.

### 첫 실행 시 참고 사항(서명되지 않은 빌드)

- **macOS**: `.dmg`를 열고 **AnkiMiner**를 **응용 프로그램**으로 끌어다 놓은 다음 실행하세요. 앱이 공증되지 않았기 때문에 첫 실행은 차단됩니다. 대화 상자를 닫고 앱을 다시 연 다음 **그래도 열기**를 선택하세요(**시스템 설정** -> **개인정보 보호 및 보안**에도 있습니다). 한 번만 하면 됩니다.
- **Linux AppImage**: 실행하기 전에 실행 권한을 주세요 - `chmod +x AnkiMiner-*.AppImage` 또는 파일 관리자에서 **속성** -> **실행 허용**.
- **Windows SmartScreen**: **추가 정보** -> **실행**.
- **Windows Defender 오탐**: **보호 기록**에서 복원하거나 [Microsoft에 신고](https://www.microsoft.com/en-us/wdsi/filesubmission)하세요.

<details>
<summary><strong>PyPI에서 설치(Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

일본어, 인도네시아어, 히브리어는 추가 설치가 필요 없습니다. 다른 마이닝 언어는 해당 엔진을 추가하세요.

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

아랍어와 페르시아어는 추가 패키지가 없습니다. 이 언어들의 데이터와 유럽 언어의 spaCy 모델은 앱의 설정 -> 마이닝 언어에서 다운로드하며, 위의 엔진도 모두 여기서 다운로드할 수 있습니다.

</details>

<details>
<summary><strong>소스에서 설치</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

전체 개발 환경 설정은 [CONTRIBUTING.md](../CONTRIBUTING.md)를 참조하세요.

</details>

## 탭

- **동영상** - 동영상/자막 한 쌍, 일괄 처리 폴더, YouTube 또는 Bilibili URL, 또는 시리즈 전체를 빈도순으로 정렬된 하나의 덱으로 마이닝합니다.
- **오디오북** - 오디오북, 팟캐스트, 라디오, 노래(오디오 + 자막/스크립트 쌍)를 마이닝합니다.
- **읽기** - 만화(mokuro), 소설(`.epub`, `.txt`; 책 한 권 또는 폴더 전체), 단독 자막 파일, 붙여 넣은 텍스트를 마이닝합니다.
- **분석** - 마이닝 기록, 난이도 순위, 달성 기록.
- **유틸리티** - 자막 생성(로컬 Whisper), 자막 타이밍 조정(ffsubsync/alass), 미디어를 대사만 남긴 오디오로 압축, yt-dlp가 지원하는 모든 사이트에서 동영상/오디오/자막 다운로드, 기존 덱에서 학습할 가치가 있는 부분만 새 덱으로 복사, 기존 카드의 필드 보충, 만화 페이지 이미지를 OCR하여 .mokuro 파일 생성(mokuro), 오디오북을 책 텍스트에 맞춰 타이밍 조정(오디오북 싱크).
- **설정** - 설정할 수 있는 모든 항목.

## 기타 기능

- 마이닝 언어 - 일본어, 중국어, 한국어, 영어, 카탈루냐어, 독일어, 포르투갈어, 프랑스어, 스페인어, 이탈리아어, 네덜란드어, 노르웨이어(보크몰), 루마니아어, 그리스어, 핀란드어, 헝가리어, 크로아티아어, 스웨덴어, 폴란드어, 리투아니아어, 덴마크어, 튀르키예어, 인도네시아어, 러시아어, 아랍어, 태국어, 페르시아어, 슬로베니아어, 우크라이나어, 베트남어, 광둥어, 히브리어를 지원하며 설정에서 전환합니다. 일본어 외의 언어는 앱에서 엔진을 다운로드하며, 인도네시아어와 히브리어는 엔진이 필요 없습니다.
- 단어 큐레이터 - 카드를 만들기 전에 후보 단어를 모두 검토하며, 장면, 만화 페이지, 사전 항목을 나란히 볼 수 있습니다.
- 실행 취소 - 실행 결과 대화 상자에서 방금 만든 노트를 바로 삭제합니다.
- 다양한 필터: i+1, 빈도 순위 범위, 블랙리스트, 정규식, 단어 세트 등.
- 오프라인 Yomitan 사전 가져오기 - 정의, 피치 악센트, 빈도 - 우선순위에 따라 연결.
- 우선순위에 따라 연결되는 여러 빈도 목록.
- 로컬 오디오 팩, JapanesePod101, Google TTS 또는 Microsoft Edge TTS의 단어 오디오를 카드에 추가.
- 읽기 카드에 Google 번역 TTS 또는 일본어와 한국어용 Naver Papago의 문장 오디오 추가(기본값은 꺼짐).
- Yomitan 방식의 사전별 용어 해설 스타일.
- 내장 libmpv 동영상 미리 보기 - 선별 중에 단어의 장면을 재생하거나, 재생하면서 자막 타이밍을 미세 조정.
- 애니메이션 스크린샷(위의 카드 예시 참조).
- 설정 프로필 - 이름을 붙인 구성을 저장하고 헤더에서 전환.
- 마이닝한 카드 스타일 다시 적용 - 현재 카드 스타일을 이미 만든 카드에 다시 적용하며, 이전 버전에서 만든 카드에는 대신 카드 보충을 사용(유틸리티 → 카드 보충).

<details>
<summary><strong>기본 제공 테마(29개)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte(밝은 테마); Frappé, Macchiato, Mocha(어두운 테마)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus(밝은 테마), Wave(어두운 테마)
- **Rosé Pine** - Dawn(밝은 테마); Main, Moon(어두운 테마)
- **Solarized** - Light, Dark
- **Standalone** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

테마 라이선스: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
다른 테마를 추가하고 싶다면 GitHub Issue로 제안해 주세요.

</details>

<details>
<summary><strong>작동 방식</strong></summary>

1. **자막을 읽고** 텍스트를 개별 단어로 나눕니다.
2. 아직 모르는 내용어만 남도록 **필터링합니다** - 원하면 단어 큐레이터에서 목록을 직접 검토할 수 있습니다.
3. 각 줄마다 동영상에서 **스크린샷과 오디오 클립을 가져옵니다**.
4. 설정한 오프라인 사전에서 **정의를 조회합니다**.
5. **완성된 카드를 Anki로 보냅니다.**

</details>

## 권장 리소스

모든 마이닝 언어의 사전과 빈도 목록, 다운로드 링크: [RESOURCES.md](../RESOURCES.md). 설정 마법사가 마이닝 언어에 맞는 리소스 세트를 제안합니다.

<details>
<summary><strong>JMnedict 라이선스</strong></summary>

[JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html)(JMdict/EDICT 프로젝트, EDRDG, CC BY-SA 4.0)에서 파생된 번들 이름 단어 세트를 사용합니다.

</details>

## 문제 해결

| 문제                    | 해결 방법                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| "Anki에 연결할 수 없음" | Anki를 시작하고 AnkiConnect가 설치되어 있는지 확인하세요.                                  |
| "덱을 찾을 수 없음"         | 설정 -> 카드 및 Anki에서 기존 덱을 선택하세요. 덱은 자동으로 만들어지지 않으므로, 새 덱이 필요하면 먼저 Anki에서 만드세요. |
| "노트 유형을 찾을 수 없음"    | 설정 -> 카드 및 Anki에서 노트 유형의 필드 이름을 설정하세요.               |
| "ffmpeg를 찾을 수 없음"       | ffmpeg를 설치하고 PATH에 추가하세요.                                               |
| 정의를 찾을 수 없음     | 설정 -> 사전 -> 사전 추가…에서 Yomitan 사전을 추가하세요. |
| Windows 설치 프로그램이 열리지 않음 / SmartScreen 경고 | [첫 실행 시 참고 사항](#첫-실행-시-참고-사항서명되지-않은-빌드) 참조: **추가 정보** -> **실행**을 선택하세요. Defender 오탐은 **보호 기록**에서 복원하세요. |
| 새로 설치했는데 정의가 없음 | 도구 -> 설정 마법사 또는 도구 -> 권장 리소스 다운로드를 실행하세요. 직접 가져올 때는 Yomitan ZIP 파일을 그대로 두세요(압축을 풀지 마세요). |
| 사전 추가가 멈추거나 실패함 | 마지막으로 표시된 단계를 기록하고 로그를 첨부하세요(아래의 "로그는 어디에 있습니까?" 참조). 보고서에 사전 ZIP 파일 이름, 출처, 크기를 포함하세요. |
| 로그는 어디에 있습니까?      | 도움말 -> 로그 폴더 열기를 사용하거나, Windows에서는 `%USERPROFILE%\.anki_miner\anki_miner.log`, macOS/Linux에서는 `~/.anki_miner/anki_miner.log`를 여세요. 순환된 로그에는 `.1`부터 `.5`까지의 접미사가 붙습니다. `anki_miner.crash`가 있으면 함께 보내 주세요(앱을 종료시킨 충돌은 로그가 아니라 이 파일에 스택을 기록합니다). 보조 프로세스의 출력이 담긴 `anki_miner.child.log`도 함께 보내 주세요. |
| 버그 보고          | 도움말 -> 진단 정보 내보내기…는 선택한 위치에 ZIP 파일을 저장합니다. 이 파일에는 로그(`anki_miner.log`와 순환된 로그, `anki_miner.crash`, `anki_miner.child.log`), `settings.json`, 구성 및 UI 상태 파일, 대기열 스냅샷과 다운로드 매니페스트, 그리고 컴퓨터와 앱 상태에 대해 생성된 보고서(`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`)가 들어 있습니다. 컴퓨터의 파일 경로와 파일 이름이 포함되므로 업로드하기 전에 검토하세요. 자동으로 업로드되는 것은 없습니다. |
| 더 자세한 진단 로그 | Anki Miner를 시작하기 전에 `ANKI_MINER_LOG_LEVEL=DEBUG`를 설정하면 서드파티 yt-dlp, urllib3, fugashi의 세부 정보까지 기록됩니다. 기본값은 `WARNING`이며, Anki Miner 자체 로그는 DEBUG 수준으로 유지됩니다. |
| 오디오 언어가 잘못됨  | 마이닝 언어의 오디오 트랙을 선택하고, 없으면 첫 번째 트랙을 사용합니다. 오디오 트랙…(동영상 -> 단일)에서 직접 선택하세요. |
| 자막이 맞지 않음    | GUI의 자막 오프셋 컨트롤을 사용하세요(범위 ±300초).                 |

## 기여

어떤 종류의 기여든 환영합니다.
프로젝트를 응원하고 싶다면 도움이 될 만한 사람들에게 공유해 주세요.

- 처음이신가요? [CONTRIBUTING.md](../CONTRIBUTING.md)부터 시작하세요.
- 아키텍처 개요: [ARCHITECTURE.md](../ARCHITECTURE.md).
- 행동 강령: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- 보안: [SECURITY.md](../SECURITY.md).

버그 보고 및 기능 요청 -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
일반적인 질문과 토론 -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) 또는 [Discord](https://discord.com/invite/aDtQyZzUVP).

## 감사의 말

프로젝트에 특별한 기여를 해 주신 분들께 진심으로 감사드립니다.

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - 훌륭한 기능 제안, 새 릴리스 테스트, 커뮤니티 구축.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - 뛰어난 기능 제안, Discord 커뮤니티 구축 및 운영.

프로젝트에 기여해 주신 모든 분은 [CONTRIBUTORS.md](../CONTRIBUTORS.md)에서 확인하세요.


## 라이선스

GNU General Public License v3.0. [LICENSE](../LICENSE)를 참조하세요.
