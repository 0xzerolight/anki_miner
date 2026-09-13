# fr_core_news_sm

Anki Miner uses spaCy's `fr_core_news_sm` 3.8.0 as the French tagger and
lemmatiser. The model is licensed LGPL-LR, the Lesser General Public License
for Linguistic Resources (it is trained on UD French Sequoia v2.8, LGPL-LR); the
full licence text is in `LICENSE` and the source pointer in `SOURCES.txt`.

The release artifact ships no part of the model. It is excluded from the
PyInstaller graph and arrives as a language pack the application downloads on
demand: the unmodified wheel published by Explosion, verified against the
SHA-256 digest pinned in `anki_miner/languages/fr/pack.py` and extracted to
`~/.anki_miner/language_packs/fr/`, where the wheel's own `LICENSE` and
`LICENSES_SOURCES` files stay beside the model data. This notice ships with the
application regardless, because the application is what delivers the model to
the user.

A card mined in French can therefore carry two licences in its provenance:
this model's LGPL-LR (which chose the word and its lemma) and the CC BY-SA 4.0
of the Wiktionary dictionary that defines it.
