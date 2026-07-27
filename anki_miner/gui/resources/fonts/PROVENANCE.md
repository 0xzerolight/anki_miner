# Bundled font provenance

One font ships with Anki Miner, and only as a last resort: it is registered at
startup **only** when the machine's own font database lists no Japanese-capable
family at all (see `anki_miner/gui/utils/fonts.py`). On any desktop that already
has a Japanese face — every Windows and macOS install, and any Linux install
with a CJK font package — this file is never loaded. Decision D44-B: the
interface and fixed-width faces come from the operating system; no Latin
typeface is bundled.

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

## Verifying

```sh
sha256sum anki_miner/gui/resources/fonts/NotoSansJP-Regular.otf
```

must print the digest in the table above. `scripts/check_wheel_assets.py`
asserts both files are present on disk and inside the built wheel; the
PyInstaller bundle picks the directory up through the whole-`resources` tree
already declared in `anki_miner.spec`.
