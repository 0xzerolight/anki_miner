# it_core_news_sm

Anki Miner mines Italian with spaCy's `it_core_news_sm` 3.8.0 pipeline. The model
is licensed CC BY-NC-SA 3.0 (`LICENSE`); it was trained on UD Italian ISDT v2.8
(Bosco, Lenci, Montemagni, Simi; CC BY-NC-SA 3.0) and WikiNER (Nothman, Ringland,
Radford, Murphy, Curran; CC BY 4.0), listed with their licence texts in
`LICENSES_SOURCES`. Both files are copied unmodified from the model wheel.

The release artifact ships no model. The model arrives as a language pack the
application downloads on demand: the unmodified wheel from
https://github.com/explosion/spacy-models/releases/tag/it_core_news_sm-3.8.0,
verified against the SHA-256 digest pinned in `anki_miner/languages/it/pack.py`
and extracted to `~/.anki_miner/language_packs/it/`. This notice ships with the
application regardless, because the application is what delivers the model to
the user.
