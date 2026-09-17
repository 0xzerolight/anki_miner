# ro_core_news_sm (Romanian spaCy model)

Anki Miner mines Romanian with spaCy's `ro_core_news_sm` 3.8.0 model, licensed
**CC BY-SA 4.0** by Explosion AI; the full licence text is in [`LICENSE`](LICENSE).
The model is trained on:

- UD Romanian RRT v2.8 — Verginica Barbu Mititelu, Elena Irimia, Cenel-Augusto Perez, Radu Ion,
  Radu Simionescu, Martin Popel (CC BY-SA 4.0), https://github.com/UniversalDependencies/UD_Romanian-RRT
- RONEC, the Romanian Named Entity Corpus (ca9ce460) — Stefan Daniel Dumitrescu, Andrei-Marius Avram,
  Luciana Morogan, Stefan Toma (MIT), https://github.com/dumitrescustefan/ronec

The release artifact ships no model. It arrives as a language pack the application
downloads on demand: the unmodified wheel from
https://github.com/explosion/spacy-models/releases/tag/ro_core_news_sm-3.8.0,
verified against the SHA-256 digest pinned in `anki_miner/languages/ro/pack.py`
and extracted to `~/.anki_miner/language_packs/ro/`. The wheel's own
`LICENSES_SOURCES` carries each source's licence text. This notice ships with the
application because the application is what delivers the model to the user.
