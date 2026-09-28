# HKCanCor (Hong Kong Cantonese Corpus)

`anki_miner/languages/yue/data/jyutping_overrides.txt` is derived from HKCanCor
(Luke and Wong 2015), licensed under **CC BY 4.0** — the full text is in
[`LICENSE.CC-BY-4.0`](LICENSE.CC-BY-4.0). Anki Miner is GPL-3.0-or-later; CC BY 4.0
permits adapted material under that licence, and this notice carries the required
attribution.

## Upstream provenance

- Source: the HKCanCor transcriptions bundled with `pycantonese` 5.0.0
  (`pycantonese/data/hkcancor/`), whose own `LICENSE.txt` is CC BY 4.0.
- Built by `scripts/build_yue_jyutping_overrides.py`, which records the thresholds
  and the hand review.

## Changes from the corpus

The file is not a copy of the corpus. It keeps, for a few dozen common words, the
jyutping reading HKCanCor's speakers use most, with the count of tokens read that
way (`聽 teng1 176/176`). Words whose dominant tag is a sentence-final particle or an
interjection are left out.
