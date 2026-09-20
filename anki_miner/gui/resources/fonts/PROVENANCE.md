# Bundled font provenance

Fonts ship with Anki Miner only as a last resort, and none replaces a face the
machine already has. Decision D44-B: the interface and fixed-width faces come
from the operating system; no Latin typeface is bundled.

- **Noto Sans JP** is registered at startup **only** when the machine's own
  font database lists no Japanese-capable family at all (see
  `anki_miner/gui/utils/fonts.py`). On any desktop that already has a Japanese
  face — every Windows and macOS install, and any Linux install with a CJK font
  package — this file is never loaded. `OFL.txt` is its licence.
- **A mining language's face** — the file a language profile names in
  `ContentTextStyle.bundled_fallback` — is registered only while that language
  is the active mining language, and only when none of the families the profile
  lists is installed for its script (`resolve_content_families` in the same
  module). Each such face has its own section below carrying its SHA-256, and
  its own licence file beside it, `OFL-<Name>.txt`: the upstream OFL with that
  project's copyright line.

## NotoSansJP-Regular.otf

| | |
|---|---|
| Family | Noto Sans JP |
| Project | [Noto CJK](https://github.com/notofonts/noto-cjk) |
| Release | [Sans 2.004](https://github.com/notofonts/noto-cjk/releases/tag/Sans2.004) |
| Commit | `523d033d6cb47f4a80c58a35753646f5c3608a78` |
| Artifact | `Sans/SubsetOTF/JP/NotoSansJP-Regular.otf`, shipped in the release asset `16_NotoSansJP.zip` |
| SHA-256 | `dff723ba59d57d136764a04b9b2d03205544f7cd785a711442d6d2d085ac5073` |
| Modified | **No.** Byte-for-byte the upstream artifact. |
| Licence | SIL Open Font License 1.1 — `OFL.txt` beside this file |

`OFL.txt` is the unmodified `LICENSE` file from the same release asset
(SHA-256 `6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2`),
also published at
<https://github.com/googlefonts/noto-cjk/blob/main/Sans/LICENSE>.

The OFL permits bundling and redistribution provided the font is not sold on its
own, the licence travels with it, and — if it were modified — the Reserved Font
Name were changed. Nothing here is modified, so the name stays.

## NotoNaskhArabic-Regular.ttf

| | |
|---|---|
| Family | Noto Naskh Arabic |
| Project | [Noto Arabic](https://github.com/notofonts/arabic) |
| Release | [NotoNaskhArabic-v2.021](https://github.com/notofonts/arabic/releases/tag/NotoNaskhArabic-v2.021) |
| Commit | `59f5a3fd985bf24858915c3dddfc51a537640965` |
| Artifact | `NotoNaskhArabic/hinted/ttf/NotoNaskhArabic-Regular.ttf`, shipped in the release asset `NotoNaskhArabic-v2.021.zip` |
| SHA-256 | `6f0a92031367b2f5a2078fe9d24f3433122b61a0bad57c423aad8f3c39aa2e6e` |
| Modified | **No.** Byte-for-byte the upstream artifact. |
| Licence | SIL Open Font License 1.1 — `OFL-NotoNaskhArabic.txt` beside this file |

The hinted TTF is the build Google Fonts ships, the one that renders legibly at
small sizes on Windows. `OFL-NotoNaskhArabic.txt` is the release asset's own
`OFL.txt` (SHA-256
`a7a5a25eb188bf1cd96982030d53e23c33485c69b1044a562254226857ee13af`). The face is
registered only while Arabic is the active mining language, and only when no
installed family among the profile's list supports the Arabic writing system.

## Verifying

```sh
sha256sum anki_miner/gui/resources/fonts/<file>
```

must print the digest in that file's table. `scripts/check_wheel_assets.py`
asserts every face and licence listed in its `REQUIRED_ASSETS` is present on
disk and inside the built wheel; the PyInstaller bundle picks the directory up
through the whole-`resources` tree already declared in `anki_miner.spec`.
