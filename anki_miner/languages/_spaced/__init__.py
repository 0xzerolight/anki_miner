"""Shared substrate for space-delimited, cased, inflected mining languages (spec §4.1).

NOT a language: the leading underscore keeps it out of ``AVAILABLE_LANGUAGES``
and ``registry._discover``. Every mechanism here takes its language data as an
argument and branches on no language code; ``languages/<code>/`` supplies the
data. Nothing in this package imports an engine, Qt or ``anki_miner.gui`` at
module import — ``spacy`` is imported function-locally by ``tokenizer.py`` — so
every spaCy profile builds on a machine without spaCy.
"""

from __future__ import annotations
