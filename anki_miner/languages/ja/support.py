"""JA adapters over the existing Japanese implementations.

Wrap-in-place (spec 3): nothing moves out of ``anki_miner.services`` — every
body here is a one-line delegation, so the ja path stays byte-identical.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import Any

from anki_miner.languages.profile import ScriptFilterOption
from anki_miner.models.word import select_mined_form
from anki_miner.services.dictionary import storage
from anki_miner.services.morphology import extract_reading
from anki_miner.utils.text_utils import (
    generate_furigana_from_tokens,
    generate_reading_from_tokens,
    is_hiragana_only,
    is_katakana_only,
    is_mixed_kana_only,
)

# Codepoint-equivalent transliteration of ``services/anki_service.py``'s
# ``_JAPANESE_RE`` — the same four ranges written as literal characters rather
# than ``\uXXXX`` escapes, so the two patterns are NOT byte-identical and a
# textual diff of the sources will not prove them equal. What must hold is that
# they match the same set of characters, which
# ``test_ja_profile.py::test_contains_target_script_is_the_anki_service_regex``
# pins against the real ``_JAPANESE_RE``, block edge by block edge. Stage 1B.1
# makes AnkiService resolve its gate from JaScriptSupport.contains_target_script;
# until then this is a copy rather than an import because ``anki_service`` is a
# heavy, network-facing module and 1B.1 would otherwise close an import cycle
# through it.
_JAPANESE_RE = re.compile(r"[぀-ゟ゠-ヿ一-鿿㐀-䶿]")

#: Option ids the settings script-filter section renders, mapped to the
#: predicate that decides a form. ``mixed_kana_only`` has no config field of its
#: own: the filter drops mixed kana only when BOTH kana boxes are ticked.
_SCRIPT_PREDICATES: dict[str, Callable[[str], bool]] = {
    "hiragana_only": is_hiragana_only,
    "katakana_only": is_katakana_only,
    "mixed_kana_only": is_mixed_kana_only,
}

# Labels are the untranslated source strings the settings panel already shows;
# ``languages/`` must not import Qt, so tr() stays with the GUI.
_SCRIPT_OPTIONS: tuple[ScriptFilterOption, ...] = (
    ScriptFilterOption(
        option_id="hiragana_only",
        label="Exclude Hiragana-Only Words",
        config_field="exclude_hiragana_only_words",
    ),
    ScriptFilterOption(
        option_id="katakana_only",
        label="Exclude Katakana-Only Words",
        config_field="exclude_katakana_only_words",
    ),
    ScriptFilterOption(
        option_id="mixed_kana_only",
        label="Exclude Mixed-Kana Words",
        config_field="",
    ),
)


class JaMinedForm:
    """Card-front spelling — ``models.word.select_mined_form`` verbatim."""

    def mined_form(
        self,
        pos: str | None,
        orth_base: str,
        lemma: str,
        surface: str,
        pronunciation: str | None = None,
    ) -> str:
        return select_mined_form(pos, orth_base, lemma, surface, pronunciation)


class JaLookupStrategy:
    """Lookup-miss fallbacks — the deinflector-backed JA candidate ladder."""

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        from anki_miner.services.definition_service import DefinitionService

        return DefinitionService._fallback_candidates(word, orth_base, ctype)


class JaReadingSupport:
    """Word reading — ``morphology.extract_reading`` verbatim."""

    def word_reading(self, token: Any) -> str:
        return extract_reading(token)


class JaSentenceAnnotator:
    """Sentence furigana plus its plain-kana reading, as the parser emits them."""

    def annotate_sentence(self, text: str, tokens: Any) -> tuple[str, str]:
        return generate_furigana_from_tokens(tokens, text=text), generate_reading_from_tokens(tokens)


class JaScriptSupport:
    """Kana script filters and the Japanese-script gate."""

    def filter_options(self) -> tuple[ScriptFilterOption, ...]:
        return _SCRIPT_OPTIONS

    def matches(self, option_id: str, form: str) -> bool:
        predicate = _SCRIPT_PREDICATES.get(option_id)
        return False if predicate is None else predicate(form)

    def contains_target_script(self, text: str) -> bool:
        return _JAPANESE_RE.search(text) is not None


class JaDictKeys:
    """Dictionary key folding and render-path homograph scope."""

    def fold_term(self, s: str) -> str:
        return unicodedata.normalize("NFC", s)

    def fold_reading(self, s: str | None) -> str | None:
        return storage._fold_reading(s)

    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
        return storage._homograph_keep_mask(word, rows, lemma)
