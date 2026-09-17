# fi_core_news_sm (Finnish spaCy model)

Anki Miner mines Finnish with spaCy's `fi_core_news_sm` 3.8.0 model, licensed
**CC BY-SA 4.0** by Explosion AI; the full licence text is in [`LICENSE`](LICENSE).
The model is trained on:

- UD Finnish TDT v2.8 — Ginter, Filip; Kanerva, Jenna; Laippala, Veronika; Miekka, Niko;
  Missilä, Anna; Ojala, Stina; Pyysalo, Sampo (CC BY-SA 4.0),
  https://github.com/UniversalDependencies/UD_Finnish-TDT
- TurkuONE (ffe2040e) — Jouni Luoma, Li-Hsin Chang, Filip Ginter, Sampo Pyysalo (CC BY-SA 4.0),
  https://github.com/TurkuNLP/turku-one

The release artifact ships no model. It arrives as a language pack the application
downloads on demand: the unmodified wheel from
https://github.com/explosion/spacy-models/releases/tag/fi_core_news_sm-3.8.0,
verified against the SHA-256 digest pinned in `anki_miner/languages/fi/pack.py`
and extracted to `~/.anki_miner/language_packs/fi/`. The wheel's own
`LICENSES_SOURCES` carries each source's licence text. This notice ships with the
application because the application is what delivers the model to the user.
