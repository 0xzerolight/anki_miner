# hu_core_news_md (Hungarian spaCy model, HuSpaCy)

Anki Miner mines Hungarian with the `hu_core_news_md` 3.8.0 model by SzegedAI and MILAB
(https://github.com/huspacy/huspacy), licensed **CC BY-SA 4.0**; the full licence text is in
[`LICENSE`](LICENSE). The model's `meta.json` lists four training sources, two of them under
**CC BY-NC-SA 3.0**, whose full licence text is in
[`LICENSE.CC-BY-NC-SA-3.0`](LICENSE.CC-BY-NC-SA-3.0):

- UD Hungarian Szeged — Richárd Farkas, Katalin Simkó, Zsolt Szántó, Viktor Varga, Veronika Vincze
  (MTA-SZTE Research Group on Artificial Intelligence) (CC BY-NC-SA 3.0),
  https://universaldependencies.org/treebanks/hu_szeged/index.html
- NYTK-NerKor Corpus — Eszter Simon, Noémi Vadász (Department of Language Technology and Applied
  Linguistics) (CC BY-SA 4.0), https://github.com/nytud/NYTK-NerKor
- Szeged NER Corpus — György Szarvas, Richárd Farkas, László Felföldi, András Kocsor, János Csirik
  (MTA-SZTE Research Group on Artificial Intelligence) (CC BY-NC-SA 3.0),
  https://rgai.inf.u-szeged.hu/node/130
- Hungarian lg Floret vectors — Szeged AI (CC BY-SA 4.0), https://huggingface.co/huspacy/hu_vectors_web_lg

The release artifact ships no model. It arrives as a language pack the application downloads on
demand: the unmodified wheel `hu_core_news_md-any-py3-none-any.whl` from
https://huggingface.co/huspacy/hu_core_news_md/tree/v3.8.0, verified against the SHA-256 digest
pinned in `anki_miner/languages/hu/pack.py` and extracted to `~/.anki_miner/language_packs/hu/`.
The wheel carries no licence file of its own, so both texts are the Creative Commons legal codes.
This notice ships with the application because the application is what delivers the model to the
user.
