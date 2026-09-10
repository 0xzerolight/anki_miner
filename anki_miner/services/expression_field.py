"""Which field of a note type is the expression, decided from its own notes.

The known-words scan (``AnkiService._collect_first_field_forms``) walks every
note type in the collection. Anki's convention makes the first field the
expression, and Anki Miner's own note type is held to it
(``anki_note_builder.field_mapping_error``), but shared decks break it both
ways: Migaku's Japanese note type opens with ``Sentence`` (every subtitle line
became a known "word" and ``Target Word`` was never read), the Core 2k/6k
Optimized deck with ``Optimized-Voc-Index`` (no target script, so the deck
contributed nothing).

Nothing here is configured. Per note type, a sample of its notes decides:

1. the first field stays while nearly all of its values look like words —
   every note type that already worked is untouched;
2. otherwise the earliest later field whose NAME says "word" (``Word``,
   ``Target Word``, ``Vocabulary-Kanji``, ``単語``, …) and whose VALUES nearly
   all look like words wins — the value check is what stops Core 2k's
   ``Expression``, which holds the sentence, from being taken on its name;
3. with neither, the first field stays — the pre-feature behaviour.

Pure and Qt-free. The language arrives as ``ScriptSupport`` + ``SentenceRules``
so no call site branches on a language code, and ``services`` takes no runtime
import of ``languages`` (profile.py reaches back into services).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from anki_miner.services.anki_note_builder import _strip_for_dedup

if TYPE_CHECKING:
    from anki_miner.languages.profile import ScriptSupport, SentenceRules

# Longest dedup-normalized value still read as a word. Idioms in an expression
# field run to ~10 characters (情けは人の為ならず); subtitle lines and example
# sentences rarely stay under it.
WORD_MAX_CHARS = 12

# Share of a field's non-empty values that must look like words before the
# field counts as a word field. A word field is ~100% word-like (JPMN keys
# with a disambiguator, over-long compounds are a few percent); a sentence
# field always has a long or punctuated tail — but a short unpunctuated
# subtitle line (彼は毎日走る) is indistinguishable from a word on its own, and
# Japanese subtitles routinely omit 。, so a bare majority would let a
# sentence field through.
WORD_FIELD_MIN_SHARE = 0.9

# How many notes of one note type decide its expression field. Buffered per
# note type, so memory stays bounded whatever the collection size.
SAMPLE_NOTES = 200

# Substrings of a field name (lowercased, spaces/underscores/hyphens removed —
# the auto_map_fields normalization) that say the field holds the expression.
# Sources: Lapis/Kiku/Japanese Support (Expression), JPMN/Kaishi (Word), Migaku
# (Target Word), AJT (VocabKanji), Core 2k (Vocabulary-Kanji), jidoujisho
# (Term), Chinese Support Redux's hanzi/simplified/traditional aliases. Left
# out on purpose: language names (Japanese, Korean, Chinese) name a sentence
# as often as a word, and when they hold the word they are the first field,
# which rule 1 keeps anyway; a bare "kanji" (RTK-style decks: Keyword, Kanji)
# would turn every studied character into a known word.
_WORD_NAME_KEYWORDS: tuple[str, ...] = (
    "expression",
    "word",
    "vocab",
    "term",
    "target",
    "headword",
    "lemma",
    "単語",
    "語彙",
    "表現",
    "hanzi",
    "simplified",
    "traditional",
    "汉字",
    "漢字",
    "中文",
    "简体",
    "簡體",
    "繁体",
    "繁體",
    "单词",
    "词语",
    "生词",
    "詞語",
    "hangul",
    "단어",
    "어휘",
)

# A name carrying one of these is never the expression, even beside a word
# keyword: WordReading, VocabFurigana, ExpressionAudio, Target Word Pitch,
# SentKanji. Readings and furigana matter most — their values ARE word-like.
_NOT_WORD_NAME_KEYWORDS: tuple[str, ...] = (
    "reading",
    "furigana",
    "kana",
    "yomi",
    "pinyin",
    "roma",
    "audio",
    "sound",
    "meaning",
    "definition",
    "def",
    "gloss",
    "translation",
    "picture",
    "image",
    "pitch",
    "freq",
    "sentence",
    "sent",
    "example",
    "hint",
    "note",
    "index",
    "tag",
)


def _normalized_name(name: str) -> str:
    return name.lower().replace(" ", "").replace("_", "").replace("-", "")


def names_word_field(name: str) -> bool:
    """Whether a field NAME says it holds the expression."""
    key = _normalized_name(name)
    if any(bad in key for bad in _NOT_WORD_NAME_KEYWORDS):
        return False
    return any(good in key for good in _WORD_NAME_KEYWORDS)


def is_word_like(text: str, *, script: ScriptSupport, sentence_rules: SentenceRules) -> bool:
    """Whether a dedup-normalized value reads as one word of the mining language.

    In the target script, at most :data:`WORD_MAX_CHARS` characters, and free
    of the language's sentence terminators and ellipses. Furigana brackets
    (``食[た]べる``) survive ``_strip_for_dedup`` and pass.
    """
    if not text or len(text) > WORD_MAX_CHARS:
        return False
    if not script.contains_target_script(text):
        return False
    punctuation = sentence_rules.terminators | sentence_rules.ellipses
    return not any(ch in punctuation for ch in text)


def _field_values(samples: Sequence[Mapping[str, object]], name: str) -> list[str]:
    """Non-empty dedup-normalized values of ``name`` across ``samples``."""
    values: list[str] = []
    for fields in samples:
        info = fields.get(name)
        if not isinstance(info, Mapping):
            continue
        raw = info.get("value")
        if not isinstance(raw, str):
            continue
        value = _strip_for_dedup(raw)
        if value:
            values.append(value)
    return values


def _holds_words(values: Sequence[str], *, script: ScriptSupport, sentence_rules: SentenceRules) -> bool:
    """At least :data:`WORD_FIELD_MIN_SHARE` of the values are word-like; no values is a no."""
    if not values:
        return False
    hits = sum(1 for value in values if is_word_like(value, script=script, sentence_rules=sentence_rules))
    return hits >= math.ceil(WORD_FIELD_MIN_SHARE * len(values))


def choose_expression_field(
    field_names: Sequence[str],
    samples: Sequence[Mapping[str, object]],
    *,
    script: ScriptSupport,
    sentence_rules: SentenceRules,
) -> str:
    """The field of a note type to read as the expression (module docstring, rules 1-3).

    Args:
        field_names: The note type's fields in order; the first is Anki's default.
        samples: notesInfo ``fields`` dicts of that note type (any number, may be empty).
    """
    first = field_names[0]
    if _holds_words(_field_values(samples, first), script=script, sentence_rules=sentence_rules):
        return first
    for name in field_names[1:]:
        if not names_word_field(name):
            continue
        if _holds_words(_field_values(samples, name), script=script, sentence_rules=sentence_rules):
            return name
    return first


class ExpressionFieldResolver:
    """Streams notesInfo rows and decides each note type's expression field once.

    :meth:`feed` returns the ``(fields, field_name)`` pairs that are ready to
    read: nothing while a note type is still being sampled, the whole buffer the
    moment its sample fills, one pair per row after that. :meth:`flush` decides
    the note types that never filled a sample. No AnkiConnect request is made
    here — the caller's batches are the only source of rows.
    """

    def __init__(
        self,
        *,
        script: ScriptSupport,
        sentence_rules: SentenceRules,
        sample_notes: int = SAMPLE_NOTES,
    ) -> None:
        self._script = script
        self._sentence_rules = sentence_rules
        self._sample_notes = sample_notes
        self._pending: dict[str, list[Mapping[str, object]]] = {}
        self._first_fields: dict[str, str] = {}
        #: note type -> the field read as its expression, once decided.
        self.chosen: dict[str, str] = {}

    def feed(self, model: object, fields: Mapping[str, object]) -> list[tuple[Mapping[str, object], str]]:
        """Offer one non-empty ``fields`` dict; return the rows now ready to read.

        A row without a note type name resolves to its first field at once
        (Anki's convention, and the shape bare unit-test rows take).
        """
        if not isinstance(model, str):
            return [(fields, next(iter(fields)))]
        chosen = self.chosen.get(model)
        if chosen is not None:
            return self._resolve(fields, chosen)
        buffer = self._pending.setdefault(model, [])
        buffer.append(fields)
        if len(buffer) < self._sample_notes:
            return []
        return self._decide(model)

    def flush(self) -> list[tuple[Mapping[str, object], str]]:
        """Decide every note type still being sampled and return its rows."""
        ready: list[tuple[Mapping[str, object], str]] = []
        for model in list(self._pending):
            ready.extend(self._decide(model))
        return ready

    def overrides(self) -> dict[str, str]:
        """Note types read from a field other than their first — the scan receipt."""
        return {model: name for model, name in self.chosen.items() if name != self._first_fields[model]}

    def _decide(self, model: str) -> list[tuple[Mapping[str, object], str]]:
        buffer = self._pending.pop(model)
        # notesInfo preserves field order and every note of a note type has the
        # same fields, so the first row is representative (deck_filter.inspect_deck).
        field_names = list(buffer[0])
        chosen = choose_expression_field(field_names, buffer, script=self._script, sentence_rules=self._sentence_rules)
        self._first_fields[model] = field_names[0]
        self.chosen[model] = chosen
        return [pair for fields in buffer for pair in self._resolve(fields, chosen)]

    @staticmethod
    def _resolve(fields: Mapping[str, object], chosen: str) -> list[tuple[Mapping[str, object], str]]:
        # Anki gives every note of a note type every field, so a row lacking
        # the chosen one is malformed; skipping it beats reading the first
        # field of a note type already judged sentence-first.
        return [(fields, chosen)] if chosen in fields else []
