# local-audio-yomichan-derived audio-pack code — license and provenance

Portions of `anki_miner/services/audio_packs/formats.py` are Python ports of
audio-pack parser code from
[local-audio-yomichan](https://github.com/yomidevs/local-audio-yomichan),
Copyright (c) 2023 Austin S., licensed under the **MIT License** — the full
text is in [`LICENSE`](LICENSE). Anki Miner is GPL-3.0-or-later; the MIT terms
permit inclusion under that license, and this notice preserves the required
attribution.

## Upstream provenance

| Ported symbol | Upstream source |
|---------------|-----------------|
| `formats.parse_ozk5` | `plugin/source/ozk5.py` (`OZK5AudioSource.add_entries`) |
| `formats.parse_nhk16` (`kanjiNotUsed` filter + numeric-headword expansion) | `plugin/source/nhk16.py` (`NHK16AudioSource.add_entries`) |
| `formats._split_headwords` | `plugin/source/nhk16.py` (`NHK16AudioSource.parse_headwords`) |
| `formats._nhk16_numbers` / `formats._NHK16_NUM_MAP` / `formats._NUM2FULLWIDTH` | `plugin/source/nhk16.py` (`NHK16AudioSource.get_numbers`, `num_map`, `num2fullwidth`) |

Pinned upstream commit: `2cbabbc75b4195b75033adf059d2a5ff037f60a6`.

## Deviations from upstream

- Parsers yield `AudioPackRow` values (Anki Miner's importer seam) instead of
  writing directly to SQLite via `INSERT`.
- The `display` column is left unset for ozk5/nhk16: it is cosmetic (never read
  back by the fetcher), so upstream's katakana-mora / pitch-accent display
  rendering is intentionally not reproduced.
- `kanjiNotUsed` filtering is done with a list comprehension rather than
  upstream's in-place `list.remove` during iteration (which skips elements).
- `get_numbers` / `_nhk16_numbers` degrades to available forms instead of
  raising `KeyError` on numbers above 100 outside `{1000, 10000}`.
