# pl_core_news_sm

Anki Miner uses spaCy's `pl_core_news_sm` 3.8.0 as the Polish tagger and
lemmatiser. The model is licensed GPL-3.0 (it is trained on UD Polish PDB v2.8
and the National Corpus of Polish, both GPL-3.0); the full licence text is in
`COPYING.GPLv3` and the source pointer in `SOURCES.txt`.

The release artifact ships no part of the model. It is excluded from the
PyInstaller graph and arrives as a language pack the application downloads on
demand: the unmodified wheel published by Explosion, verified against the
SHA-256 digest pinned in `anki_miner/languages/pl/pack.py` and extracted to
`~/.anki_miner/language_packs/pl/`, where the wheel's own `LICENSE` and
`LICENSES_SOURCES` files stay beside the model data. This notice ships with the
application regardless, because the application is what delivers the model to
the user.

A card mined in Polish can therefore carry two licences in its provenance:
this model's GPL-3.0 (which chose the word and its lemma) and the CC BY-SA 4.0
of the Wiktionary dictionary that defines it.
