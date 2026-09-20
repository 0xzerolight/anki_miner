# Wiktionary

Wiktionary content is licensed **CC BY-SA 4.0** — the full text is in
[`LICENSE.CC-BY-SA-4.0`](LICENSE.CC-BY-SA-4.0). Two things in Anki Miner derive
from it.

## `anki_miner/languages/fa/data/compound_verbs.tsv`

830 Persian compound-verb rows (`noun` TAB `light-verb infinitive`), derived
from the `wty-fa-en` Yomitan build by taking every two-token verb headword whose
second token is an infinitive listed in hazm's `verbs.dat`. The build the file
was derived from is `wty-fa-en` revision **2026.09.19**, 3,328,547 bytes, sha256
`0654d5b24ae8b405b0959909a2947e13d7c6df8564607aba215f0edc2e5931a9`. This is a
derivative work and ships under the same licence; the file's own header records
the same provenance.

Chain of attribution: English Wiktionary → kaikki.org → `wiktionary-to-yomitan`
(https://github.com/yomidevs/wiktionary-to-yomitan) → this file.

## The `wty-*` dictionaries the application downloads

The recommended dictionaries in every language's `catalog.py` whose id starts
with `wty-` are the same builds, downloaded on demand from upstream and never
redistributed here. Their `index.json` carries `attribution:
https://kaikki.org/`, and the application shows the licence note beside each
one before it downloads.
