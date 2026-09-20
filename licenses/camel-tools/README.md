# camel-tools

Anki Miner's Arabic analyzer (`anki_miner/languages/ar/_calima/`) is a port of the
morphological analyzer in CAMeL Tools, copyright 2018-2026 New York University Abu Dhabi,
released under the MIT licence reproduced in `LICENSE`.

Source: https://github.com/CAMeL-Lab/camel_tools, commit
`be79ca9fc493f0df795375a7255bafef246a802d` (version 1.6.0).

Ported, analysis only: `camel_tools/morphology/database.py`, `analyzer.py`, `utils.py` and
`errors.py`, and the Arabic character sets and de-diacritisation of `camel_tools/utils/`.
The port drops the builtin-database catalogue (`camel_tools.data`, which downloads a JSON
file from GitHub when first imported), generation and reinflection, the backoff modes and
the `cachetools`, `emoji` and `six` dependencies. The morphology database it reads is a
separate download with its own licence (`licenses/calima-msa-r13/`).
