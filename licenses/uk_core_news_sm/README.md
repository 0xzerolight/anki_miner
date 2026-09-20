# uk_core_news_sm

Anki Miner uses spaCy's `uk_core_news_sm` 3.8.0 as the Ukrainian tagger. The model is licensed MIT
(copyright ExplosionAI GmbH); the licence text is in `LICENSE` and the source pointer in
`SOURCES.txt`. Its training data is the Ukr-Synth corpus (MIT, Volodymyr Kurnosov). Its lemmatiser
reads the pymorphy3 Ukrainian dictionaries, whose notice is `licenses/pymorphy3_dicts_uk/`.

The release artifact ships no part of the model. It is excluded from the PyInstaller graph and
arrives as a language pack the application downloads on demand: the unmodified wheel published by
Explosion, verified against the SHA-256 digest pinned in `anki_miner/languages/uk/pack.py` and
extracted to `~/.anki_miner/language_packs/uk/`, where the wheel's own `LICENSE` and
`LICENSES_SOURCES` files stay beside the model data. This notice ships with the application
regardless, because the MIT licence asks that its notice travel with copies and the application is
what delivers the model to the user.
