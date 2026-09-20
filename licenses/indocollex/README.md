# IndoCollex (Indonesian colloquial -> formal pairs)

`anki_miner/languages/id/colloquial.py` is derived from IndoCollex (Wibowo et al.,
"IndoCollex: A Testbed for Morphological Transformation of Indonesian Colloquial Words",
Findings of ACL-IJCNLP 2021), [haryoa/indo-collex](https://github.com/haryoa/indo-collex),
Copyright (c) 2021 Haryo Akbarianto Wibowo, licensed under the **MIT License** — the full text
is in [`LICENSE`](LICENSE). Anki Miner is GPL-3.0-or-later; the MIT terms permit inclusion under
that licence, and this notice preserves the required attribution.

## Upstream provenance

- Pinned commit: `df626e812cd5924794fabf40003368a69f4e953c`.
- File: `dict/inforformal-formal-Indonesian-dictionary.tsv` (2,622 `informal<TAB>formal` pairs).

## Deviations from upstream

- Kept only pairs whose informal side is lowercase letters (hyphens allowed) and whose every
  formal word is a headword of wty-id-en revision 2026.09.19 (used as a filter only; nothing of
  it is copied). This drops digit spellings (`10rb`) and unattested targets.
- A 28-pair hand-curated core from Anki Miner's design (spec C.5) is laid over the result
  (`lo`/`lu` -> `kamu`, `bgt`/`banget` -> `sangat`, `bikin` -> `membuat`, ...).
- Stored as a Python table (1,988 pairs), not as the upstream TSV.
