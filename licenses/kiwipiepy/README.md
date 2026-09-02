# kiwipiepy

Anki Miner uses kiwipiepy (Kiwi) as the Korean morphological analyser.
kiwipiepy is licensed LGPL-3.0-or-later; the full licence text is in
`COPYING.LGPLv3` and the source pointer in `SOURCES.txt`.

The release artifact ships no kiwipiepy at all. The engine and its model are
excluded from the PyInstaller graph and arrive instead as a language pack the
application downloads on demand: unmodified PyPI artifacts, verified against
SHA-256 digests pinned in `anki_miner/languages/ko/pack.py` and extracted to
`~/.anki_miner/language_packs/ko/`. The extension module and the model data are
therefore ordinary files in the user's own home directory, and replacing them
with a modified build of the same version requires no rebuild of the
application - which is the LGPL relinking/replaceability requirement for this
configuration. This notice ships with the application regardless, because the
application is what delivers the engine to the user.
