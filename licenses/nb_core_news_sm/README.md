# nb_core_news_sm

Anki Miner uses spaCy's `nb_core_news_sm` 3.8.0 as the Norwegian Bokmål tagger
and lemmatiser. The model is licensed MIT (copyright ExplosionAI GmbH); the
licence text is in `LICENSE` and the source pointer in `SOURCES.txt`. Its
training data, UD Norwegian Bokmaal and NorNE, is public domain (CC0).

The release artifact ships no part of the model. It is excluded from the
PyInstaller graph and arrives as a language pack the application downloads on
demand: the unmodified wheel published by Explosion, verified against the
SHA-256 digest pinned in `anki_miner/languages/nb/pack.py` and extracted to
`~/.anki_miner/language_packs/nb/`, where the wheel's own `LICENSE` and
`LICENSES_SOURCES` files stay beside the model data. This notice ships with the
application regardless, because the MIT licence asks that its notice travel
with copies and the application is what delivers the model to the user.
