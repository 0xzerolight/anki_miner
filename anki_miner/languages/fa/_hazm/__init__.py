"""A partial, hand-written port of hazm 0.12.1 (MIT) -- see ``licenses/hazm/``.

Ported because hazm itself cannot be imported here: it declares
``Requires-Python >=3.12,<3.14``, ``hazm/types.py`` uses PEP 695 ``type X = ...``
(a SyntaxError on CI's 3.11 leg) and its package ``__init__`` imports
nltk/flashtext/tqdm at module level. This package imports the standard library
and nothing else, so it loads on every supported Python with no pack installed.

What is ported: the normaliser's character tables and affix rules (they live in
``languages/fa/script.py``, beside the fold that shares them), the word-tokenizer
split pattern, the stemmer's suffix table, and the verb paradigms -- as
composition patterns rather than hazm's ``Conjugation`` class, which is
equivalent for 689 of the 693 ``verbs.dat`` rows and costs 5.7 MB instead of
113 MB (plan probe P-3).

What is NOT ported: ``join_verb_parts`` (surfaces must stay verbatim slices of
the line), the CRF tagger, the chunker, the dependency parser, the embeddings,
and ``InformalNormalizer``'s ``informal_to_formal_conjucation`` -- whose
offset-zip mapping is wrong upstream (probe P-5: it maps miram to a three-word
string meaning "they had been going").

The Persian data files this package reads (``words.dat``, ``verbs.dat``,
``iverbs.dat``, ``iwords.dat``, ``stopwords.dat``) are hazm's own, downloaded as
the ``fa`` language pack and never bundled.
"""

from __future__ import annotations

__all__ = ["data", "stemmer", "tokenize"]
