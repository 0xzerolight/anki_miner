# pt_core_news_sm

Anki Miner mines Portuguese with spaCy's `pt_core_news_sm` 3.8.0 model. The
model is licensed **CC BY-SA 4.0**; the full licence text is in `LICENSE`, and
`LICENSES_SOURCES` lists the corpora it was trained on with their own terms:

- UD Portuguese Bosque v2.8 (Rademaker et al.), CC BY-SA 4.0
- WikiNER (Nothman et al.), CC BY 4.0

The release artifact ships no model. The application downloads the unmodified
wheel from `https://github.com/explosion/spacy-models/releases` on demand,
verified against the SHA-256 digest pinned in `anki_miner/languages/pt/pack.py`,
and extracts it to `~/.anki_miner/language_packs/pt/`. This notice ships with
the application regardless, because the application is what delivers the model
to the user, and the attribution travels with it.
