# hazm

`anki_miner/languages/fa/_hazm/` contains a partial, hand-written port of
hazm 0.12.1 (https://github.com/roshan-research/hazm), MIT licensed — the text
beside this file.

Ported: the normaliser's character tables and affix-spacing rules, the
word-tokenizer split pattern, the stemmer's suffix table, and the verb
conjugation paradigms. No hazm code is imported or vendored verbatim, and no
part of the hazm distribution is installed: hazm declares
`Requires-Python >=3.12,<3.14`, its `types.py` uses PEP 695 syntax that is a
SyntaxError on Python 3.11, and its package `__init__` imports nltk, flashtext
and tqdm at module level.

The Persian data files the port reads — `words.dat`, `verbs.dat`, `iverbs.dat`,
`iwords.dat` and `stopwords.dat` — are the hazm 0.12.1 wheel's own `hazm/data/`
members, downloaded as the Persian language pack and never bundled. They carry
the same MIT licence.

`anki_miner/languages/fa/data/colloquial.tsv` also draws on hazm's `iwords.dat`
and on shekar 1.6.3 (https://github.com/amirivojdan/shekar), MIT, © 2024 Ahmad
Amirivojdan; the file's own header records that.
