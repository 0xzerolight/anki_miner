# nl_core_news_sm (Dutch spaCy model)

Anki Miner mines Dutch with spaCy's `nl_core_news_sm` 3.8.0 model, licensed
**CC BY-SA 4.0** by Explosion AI; the full licence text is in [`LICENSE`](LICENSE).
The model is trained on:

- UD Dutch LassySmall v2.8 — Gosse Bouma, Gertjan van Noord (CC BY-SA 4.0),
  https://github.com/UniversalDependencies/UD_Dutch-LassySmall
- UD Dutch Alpino v2.8 — Daniel Zeman, Zdeněk Žabokrtský, Gosse Bouma, Gertjan van Noord (CC BY-SA 4.0),
  https://github.com/UniversalDependencies/UD_Dutch-Alpino
- Dutch NER Annotations for UD LassySmall — NLP Town (CC BY-SA 4.0), https://nlp.town

The release artifact ships no model. It arrives as a language pack the application
downloads on demand: the unmodified wheel from
https://github.com/explosion/spacy-models/releases/tag/nl_core_news_sm-3.8.0,
verified against the SHA-256 digest pinned in `anki_miner/languages/nl/pack.py`
and extracted to `~/.anki_miner/language_packs/nl/`. The wheel's own
`LICENSES_SOURCES` carries each source's licence text. This notice ships with the
application because the application is what delivers the model to the user.
