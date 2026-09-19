# calima-msa-r13

Anki Miner analyses Arabic with the CAMeL Tools `calima-msa-r13` morphology database
(format 0.4.0). The database is derived from Aramorph 1.2.1 (Buckwalter Arabic
Morphological Analyzer, Linguistic Data Consortium) and is licensed GPL-2.0; its own
licence file, reproduced in `LICENSE`, carries the copyright notices and the full
GNU General Public License version 2. The source pointers are in `SOURCES.txt`.

The release artifact ships no part of the database. It arrives as a language pack the
application downloads on demand: the unmodified `morphology_db_calima-msa-r13-0.4.0.zip`
published by the CAMeL Lab, verified against the SHA-256 digests of the zip and of the
`morphology.db` inside it, both pinned in `anki_miner/languages/ar/pack.py`, and extracted
to `~/.anki_miner/language_packs/ar/calima_msa/`, where the zip's own `LICENSE` stays
beside the database. This notice ships with the application regardless, because the
application is what delivers the database to the user.

A card mined in Arabic can therefore carry two licences in its provenance: this
database's GPL-2.0 (which chose the word's lemma and root) and the CC BY-SA 4.0 of the
Wiktionary dictionary that defines it.
