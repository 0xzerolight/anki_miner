"""In-tree port of the CAMeL Tools morphological analyzer (analysis only).

Ported from ``camel_tools`` at commit ``be79ca9fc493f0df795375a7255bafef246a802d``
(CAMeL-Lab/camel_tools, version 1.6.0), MIT licence, copyright 2018-2026 New
York University Abu Dhabi. The notice ships in ``licenses/camel-tools/``.

Why a port rather than the PyPI package: ``camel_tools.morphology.database``
imports ``camel_tools.data`` at module level, which creates ``~/.camel_tools``
and downloads a catalogue JSON from GitHub the first time it is imported. That
is an import-time network path neither the test tripwire nor a bundle smoke can
tolerate, and the package drags nine otherwise-unused dependencies.

What was kept, and from where (upstream path -> here):

* ``morphology/database.py`` -> ``database.py``: the analysis half of
  ``MorphologyDB`` (no generation or reinflection tables, no builtin catalogue).
* ``morphology/analyzer.py`` -> ``analyzer.py``: ``Analyzer.analyze`` with the
  ``NONE`` backoff mode. ``cachetools.LFUCache`` became a bounded dict and
  ``CharMapper`` became ``str.translate``.
* ``morphology/utils.py`` -> ``morph_utils.py``: ``merge_features`` and the
  rewrite rules it calls.
* ``morphology/errors.py`` -> ``errors.py``: the two errors analysis raises.
* ``utils/charsets.py`` + ``utils/dediac.py`` -> ``charsets.py``: the Arabic
  letter and diacritic sets and ``dediac_ar``. The Unicode punctuation/symbol
  set, which upstream builds by walking all 1.1 M code points at import (and
  which pulls in ``emoji``), is a ``unicodedata`` category test instead.

Nothing here imports anything outside the standard library.

Parity with upstream: every analysis dict this port returns was compared with
upstream's for the 20,000 most frequent Arabic words (hermitdave 2018 ar_50k)
plus the fixture specials - 0 mismatches. To re-derive: check out camel_tools
at be79ca9f, put stubs for ``cachetools``, ``emoji``, ``six`` and
``camel_tools.data`` on the path (the first three are only imported, the fourth
is the download path this port exists to avoid), point both implementations at
the same seeded ``morphology.db``
(``scripts/fetch_language_pack_seeds.py <root> ar``) and compare
``sorted(json.dumps(a, sort_keys=True))`` per word.
``tests/fixtures/ar/calima_parity.jsonl`` pins that digest for 17 of them, and
``tests/unit/languages/test_ar_calima_port.py`` re-checks those on every run.
"""
