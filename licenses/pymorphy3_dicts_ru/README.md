# pymorphy3-dicts-ru

Anki Miner's Russian language pack delivers `pymorphy3-dicts-ru` 2.4.417150.4580142, the Russian
dictionaries spaCy's `ru_core_news_sm` lemmatiser reads through pymorphy3. The package's Python code
is MIT-licensed. Its dictionary data is compiled from OpenCorpora (https://opencorpora.org/,
revision 417150) and is licensed under the Creative Commons Attribution-ShareAlike 3.0 licence
(https://creativecommons.org/licenses/by-sa/3.0/), as the package's own metadata states.

The release artifact ships no part of it. It is excluded from the PyInstaller graph and arrives in
the Russian language pack the application downloads on demand: the unmodified wheel published on
PyPI, verified against the SHA-256 digest pinned in `anki_miner/languages/ru/pack.py` and extracted
to `~/.anki_miner/language_packs/ru/`, together with its `.dist-info` directory (pymorphy3 finds its
dictionaries through that directory's entry point). This notice ships with the application because
the application is what delivers the data to the user.
