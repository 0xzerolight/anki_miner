# el_core_news_sm

Anki Miner mines Greek with spaCy's `el_core_news_sm` 3.8.0 pipeline. The model
is licensed CC BY-NC-SA 3.0 (`LICENSE`); it was trained on UD Greek GDT v2.8
(Prokopis Prokopidis; CC BY-NC-SA 3.0) and the Greek NER Corpus (Google Summer
of Code 2018, Giannis Daras; MIT), listed with their licence texts in
`LICENSES_SOURCES`. Both files are copied unmodified from the model wheel.

The release artifact ships no model. The model arrives as a language pack the
application downloads on demand: the unmodified wheel from
https://github.com/explosion/spacy-models/releases/tag/el_core_news_sm-3.8.0,
verified against the SHA-256 digest pinned in `anki_miner/languages/el/pack.py`
and extracted to `~/.anki_miner/language_packs/el/`. This notice ships with the
application regardless, because the application is what delivers the model to
the user.
