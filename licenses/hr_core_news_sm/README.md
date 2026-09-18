# hr_core_news_sm (Croatian spaCy model)

Anki Miner mines Croatian with spaCy's `hr_core_news_sm` 3.8.0 model, licensed
**CC BY-SA 4.0** by Explosion AI; the full licence text is in [`LICENSE`](LICENSE).
The model is trained on:

- Training corpus hr500k 1.0 — Nikola Ljubešić, Željko Agić, Filip Klubička, Vuk Batanović,
  Tomaž Erjavec (CC BY-SA 4.0), http://hdl.handle.net/11356/1183

The release artifact ships no model. It arrives as a language pack the application
downloads on demand: the unmodified wheel from
https://github.com/explosion/spacy-models/releases/tag/hr_core_news_sm-3.8.0,
verified against the SHA-256 digest pinned in `anki_miner/languages/hr/pack.py`
and extracted to `~/.anki_miner/language_packs/hr/`. The wheel's own
`LICENSES_SOURCES` carries each source's licence text. This notice ships with the
application because the application is what delivers the model to the user.
