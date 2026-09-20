# viet_text_tools-derived Vietnamese code — license and provenance

`anki_miner/languages/vi/tones.py` (the tone-mark placement fold) is a Python port of
`normalize_diacritics` from [viet_text_tools](https://github.com/enricobarzetti/viet_text_tools)
0.1.6, Copyright (c) 2020 enricobarzetti, licensed under the **MIT License** — the full text is in
[`LICENSE`](LICENSE).

## Upstream provenance

| Ported file | Upstream source |
|-------------|-----------------|
| `anki_miner/languages/vi/tones.py` (`normalize_diacritics`) | `viet_text_tools/__init__.py` (`normalize_diacritics`), sdist `viet_text_tools-0.1.6.tar.gz`, sha256 `1ea821e9a96b2f121f017ba95569dd380fd57c9f65a9695f531137b36d392730` |
| `tests/unit/languages/test_vi_tones.py` (upstream cases) | `viet_text_tools/tests/test_things.py` (`NormalizeDiacriticsTestCase`) |

Changes from upstream: the regular expressions are compiled once at import, the `decomposed`
flag is dropped (no caller wants NFD output), and two named wrappers (`to_old_style`,
`to_new_style`) are added. The rules and their order are unchanged.
