# sv_core_news_sm (Swedish spaCy model)

Anki Miner mines Swedish with spaCy's `sv_core_news_sm` 3.8.0 model, licensed
**CC BY-SA 4.0** by Explosion AI; the full licence text is in [`LICENSE`](LICENSE).
The model is trained on:

- UD Swedish Talbanken v2.8 — Joakim Nivre, Aaron Smith (CC BY-SA 4.0),
  https://github.com/UniversalDependencies/UD_Swedish-Talbanken
- Stockholm-Umeå Corpus (SUC) v3.0 — Språkbanken (CC BY 4.0),
  https://huggingface.co/datasets/KBLab/sucx3_ner

The release artifact ships no model. It arrives as a language pack the application
downloads on demand: the unmodified wheel from
https://github.com/explosion/spacy-models/releases/tag/sv_core_news_sm-3.8.0,
verified against the SHA-256 digest pinned in `anki_miner/languages/sv/pack.py`
and extracted to `~/.anki_miner/language_packs/sv/`. The wheel's own
`LICENSES_SOURCES` carries each source's licence text. This notice ships with the
application because the application is what delivers the model to the user.
