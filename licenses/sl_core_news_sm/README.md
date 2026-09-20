# sl_core_news_sm (Slovenian spaCy model)

Anki Miner mines Slovenian with spaCy's `sl_core_news_sm` 3.8.0 model, licensed
**CC BY-SA 4.0** by Explosion AI; the full licence text is in [`LICENSE`](LICENSE).
The model is trained on:

- UD Slovenian SSJ v2.11 — Kaja Dobrovoljc, Tomaž Erjavec, Simon Krek (CC BY-SA 4.0),
  https://github.com/UniversalDependencies/UD_Slovenian-SSJ
- Training corpus SUK 1.0 — Špela Arhar Holdt, Simon Krek, Kaja Dobrovoljc, Tomaž Erjavec,
  Polona Gantar, Jaka Čibej and others (CC BY-SA 4.0),
  https://www.clarin.si/repository/xmlui/handle/11356/1747

The release artifact ships no model. It arrives as a language pack the application
downloads on demand: the unmodified wheel from
https://github.com/explosion/spacy-models/releases/tag/sl_core_news_sm-3.8.0,
verified against the SHA-256 digest pinned in `anki_miner/languages/sl/pack.py`
and extracted to `~/.anki_miner/language_packs/sl/`. The wheel's own
`LICENSES_SOURCES` carries each source's licence text. This notice ships with the
application because the application is what delivers the model to the user.
