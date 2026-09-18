# da_core_news_sm (Danish spaCy model)

Anki Miner mines Danish with spaCy's `da_core_news_sm` 3.8.0 model, licensed
**CC BY-SA 4.0** by Explosion AI; the full licence text is in [`LICENSE`](LICENSE).
The model is trained on:

- UD Danish DDT v2.8 — Anders Johannsen, Héctor Martínez Alonso, Barbara Plank (CC BY-SA 4.0),
  https://github.com/UniversalDependencies/UD_Danish-DDT
- DaNE — Rasmus Hvingelby, Amalie B. Pauli, Maria Barrett, Christina Rosted, Lasse M. Lidegaard,
  Anders Søgaard (CC BY-SA 4.0),
  https://github.com/alexandrainst/danlp/blob/master/docs/datasets.md#danish-dependency-treebank-dane

The release artifact ships no model. It arrives as a language pack the application
downloads on demand: the unmodified wheel from
https://github.com/explosion/spacy-models/releases/tag/da_core_news_sm-3.8.0,
verified against the SHA-256 digest pinned in `anki_miner/languages/da/pack.py`
and extracted to `~/.anki_miner/language_packs/da/`. The wheel's own
`LICENSES_SOURCES` carries each source's licence text. This notice ships with the
application because the application is what delivers the model to the user.
