# stopwords-iso (Indonesian stopwords)

`anki_miner/languages/id/stopwords.py` is derived from
[stopwords-iso/stopwords-id](https://github.com/stopwords-iso/stopwords-id), Copyright (c) 2016
Gene Diaz, licensed under the **MIT License** — the full text is in [`LICENSE`](LICENSE). Anki
Miner is GPL-3.0-or-later; the MIT terms permit inclusion under that licence, and this notice
preserves the required attribution.

## Upstream provenance

- Pinned commit: `6109494eb4cfebd75c59bd832848374d02f4e907`.
- File: `stopwords-id.txt` (758 words).

## Deviations from upstream

- Removed the 205 words whose every sense in wty-id-en revision 2026.09.19 (following non-lemma
  redirects up to two hops) is a noun, verb, adjective or name: the upstream list is an
  information-retrieval list and holds content words (`bapak`, `bekerja`, `masalah`).
- Added colloquial function words (`gue lo nggak udah aja kayak dong sih deh` ...), the
  particle `ya` and the standalone clitic `nya`.
- Stored as a Python frozenset (572 words), not as the upstream text file.
