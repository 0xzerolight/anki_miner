# lt_core_news_sm (Lithuanian spaCy model)

Anki Miner mines Lithuanian with spaCy's `lt_core_news_sm` 3.8.0 model, licensed
**CC BY-SA 4.0** by Explosion AI; the full licence text is in [`LICENSE`](LICENSE)
and the source pointer in [`SOURCES.txt`](SOURCES.txt). The model is trained on
UD Lithuanian ALKSNIS v2.8 (CC BY-SA 4.0) and, for its named-entity component
only, the TokenMill NER Corpus, which Explosion licenses commercially; no corpus
data is redistributed, only the trained weights.

The release artifact ships no model. It arrives as a language pack the
application downloads on demand: the unmodified wheel from
https://github.com/explosion/spacy-models/releases/tag/lt_core_news_sm-3.8.0,
verified against the SHA-256 digest pinned in `anki_miner/languages/lt/pack.py`
and extracted to `~/.anki_miner/language_packs/lt/`, where the wheel's own
`LICENSE` and `LICENSES_SOURCES` files stay beside the model data. This notice
ships with the application because the application is what delivers the model
to the user.
