# zeyrek-derived Turkish analyzer code — license and provenance

`anki_miner/languages/tr/analyzer.py` ports `RuleBasedAnalyzer.search` and `RuleBasedAnalyzer.advance` from
[zeyrek](https://github.com/obulat/zeyrek) 0.1.3, Copyright (c) 2019, Olga Bulat, licensed under the
**MIT License** — the full text is in [`LICENSE`](LICENSE). zeyrek is itself a Python port of
[Zemberek-NLP](https://github.com/ahmetaa/zemberek-nlp) (Apache License 2.0). Anki Miner is GPL-3.0-or-later;
the MIT terms permit inclusion under that license, and this notice preserves the required attribution.

zeyrek itself, with its Zemberek lexicon, is not bundled: it reaches the user as the Turkish language pack
(`anki_miner/languages/tr/pack.py`, from PyPI) or through `pip install "anki-miner[tr]"`.
