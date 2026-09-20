# pymorphy3-dicts-uk

Anki Miner's Ukrainian language pack delivers `pymorphy3-dicts-uk` 2.4.1.1.1663094765, the Ukrainian
dictionaries spaCy's `uk_core_news_sm` lemmatiser reads through pymorphy3. The package's own
metadata states `License: GPLv3 License` while its classifier list still carries the MIT classifier
it inherited from its Russian sibling. The dictionary data derives from LanguageTool's `dict_uk`
word list (https://github.com/brown-uk/dict_uk), which is GPL-3.0, so Anki Miner treats the package
as GPL-3.0. Anki Miner is itself GPL-3.0, so the terms carry across unchanged.

The release artifact ships no part of it. It is excluded from the PyInstaller graph and arrives in
the Ukrainian language pack the application downloads on demand: the unmodified wheel published on
PyPI, verified against the SHA-256 digest pinned in `anki_miner/languages/uk/pack.py` and extracted
to `~/.anki_miner/language_packs/uk/`, together with its `.dist-info` directory (pymorphy3 finds its
dictionaries through that directory's entry point). This notice ships with the application because
the application is what delivers the data to the user.
