"""zh script gate, dictionary-key folding, mined-form policy, lookup ladder.

Chinese has no inflection and no kana, so the policies stay small: the mined
form is the segmented surface in the configured Character Set, term keys fold
with NFC only, and the settings script-filter section has no options at all.
"""

from __future__ import annotations

import re
from typing import Any

from anki_miner.languages.profile import ScriptFilterOption
from anki_miner.languages.zh.variants import (
    normalize_zh,
    to_script,
    to_simplified,
    to_traditional,
    variant_candidates,
)
from anki_miner.utils.ja_normalize import is_cjk_ideograph


class ZhScriptSupport:
    """No script toggles; the ingestion gate is "contains a Han ideograph"."""

    def filter_options(self) -> tuple[ScriptFilterOption, ...]:
        return ()

    def matches(self, option_id: str, form: str) -> bool:
        return False

    def contains_target_script(self, text: str) -> bool:
        return any(is_cjk_ideograph(char) for char in text)


# One rendered glossary item, as the importer writes it into ``entries.content``
# (yomitan_renderer's nested-member list and the ``<ul>`` a CC-CEDICT port's own
# structured content carries both use this class). Non-greedy: a nested list
# would clip the text, which at worst leaves the item's leading words — enough
# for the patterns below.
_GLOSS_ITEM = re.compile(r'<li class="gloss-sc-li">(.*?)</li>', re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")

# CC-CEDICT stores one row per (headword, reading, entry), and its own order
# puts a common character's surname and cross-reference rows ahead of the sense
# rows. These name a row that states nothing a learner can use: "surname Gan",
# "old variant of 乾|干[gān]", "see 基友[jīyǒu]", "used in 㐖毒[xiédú]", and the
# register-marked survivals "(classical) to kill", "long robe (old)". Counted
# over the shipped index (202,889 rows, 320,036 glosses): 997 surname glosses,
# 5,560 variant/form glosses, 7,081 "see", 1,202 "used in" — 13,252 rows are
# made of nothing else — and 1,478 further rows whose every gloss carries a
# register marker (959 literary, 386 old, 129 archaic, 14 classical, 1
# obsolete; "(arch.)" is the same marker abbreviated). "(lit.)" is NOT one: it
# means "literally". The name must be capitalised, which is what keeps 姓名's
# "surname and given name; full name" out.
_SURNAME_GLOSS = re.compile(r"surname [A-Z]")
_CROSS_REFERENCE_GLOSS = re.compile(
    r"(?:old |archaic |erhua |Japanese |\(old\) )?variant of\s|erhua form of\s|see\s|used in\s",
    re.IGNORECASE,
)
_REGISTER_MARKER = r"\((?:old|archaic|arch\.|classical|literary|obsolete)\)"
_ARCHAIC_GLOSS = re.compile(rf"^{_REGISTER_MARKER}|{_REGISTER_MARKER}$", re.IGNORECASE)


def _points_elsewhere(gloss: str) -> bool:
    """True iff ``gloss`` only names another entry.

    A cross-reference must actually point at a headword: "see you next time"
    (再見) and "used in place names" are senses that happen to open with the
    same words, so the Han character of the referent is what separates them.
    """
    return bool(_CROSS_REFERENCE_GLOSS.match(gloss)) and any(is_cjk_ideograph(char) for char in gloss)


def _states_no_live_sense(gloss: str) -> bool:
    """True iff ``gloss`` is a family name, a register-marked survival or a pointer."""
    return bool(_SURNAME_GLOSS.match(gloss)) or bool(_ARCHAIC_GLOSS.search(gloss)) or _points_elsewhere(gloss)


class ZhDictKeyFolding:
    """NFC term keys, case-folded pinyin reading keys, Rule-A homograph scope."""

    def fold_term(self, s: str) -> str:
        return normalize_zh(s)

    def fold_reading(self, s: str | None) -> str | None:
        """Fold a pinyin reading key: NFC, then casefold.

        Case varies between CC-CEDICT ports (``Zhōng Guó`` vs ``zhōng guó``)
        and the readings this engine generates. Lowering is only safe because
        it is applied SYMMETRICALLY: the zh importer folds reading keys with
        this exact function before writing them, and every lookup folds the
        query the same way. Fold on one side only and the miss is silent —
        the row is there and is never found.
        """
        return normalize_zh(s).casefold() if s is not None else None

    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
        """Rule A of ``storage._homograph_keep_mask`` (:284-287), and nothing else.

        At least one term-exact row exists => keep the term-exact rows and drop
        reading-only homographs whose gloss no term-exact row already
        contributes (the dedup-before-cap tag-union carve-out). Anything else
        keeps every row.

        Rule A' (:288-292, the tokenizer-lemma tier) and Rule B (:293-294, the
        kana-only filter) are deliberately absent: a jieba lemma is its own
        surface, so A' could never fire, and there is no kana script to filter.
        ``lemma`` is accepted to keep the one cross-language signature.
        """
        term_exact = [term == word for term, _ in rows]
        if not any(term_exact):
            return [True] * len(rows)
        exact_contents = {content for (_, content), keep in zip(rows, term_exact, strict=True) if keep}
        return [keep or content in exact_contents for (_, content), keep in zip(rows, term_exact, strict=True)]

    def sense_rank(self, content: str) -> int:
        """Where this row sorts among the rows sharing its reading priority.

        ``0`` for a row stating a live sense; ``1`` when its every gloss is a
        family name or a register-marked survival; ``2`` when its every gloss
        only points at another entry. Read by ``services/dictionary/storage.py``'s
        lookup sort, which leaves the rest of the cascade alone — nothing is
        dropped and a word whose rows all rank alike is unchanged. Without it 干
        read gān opens on "old variant of 乾|干[gān]" and 还 read huán on
        "surname Huan", because index order is all that separates rows sharing
        a reading.

        The middle rank is what keeps 刘 on "surname Liu" rather than on its
        "(classical) a type of battle-ax" row while both still lead the pure
        pointer. A row mixing a surname or an archaism with a live sense (王
        "surname Wang; king") states a sense and keeps rank 0. Content this
        cannot read as glossary items - another zh dictionary's own markup -
        ranks 0, so an unreadable dictionary is left in the order its index gave
        it.
        """
        glosses = [_HTML_TAG.sub("", item).strip() for item in _GLOSS_ITEM.findall(content)]
        present = [gloss for gloss in glosses if gloss]
        if not present or not all(_states_no_live_sense(gloss) for gloss in present):
            return 0
        return 2 if all(_points_elsewhere(gloss) for gloss in present) else 1

    def term_variants(self, term: str) -> list[str]:
        """Other-script spellings a frequency source may rank ``term`` under.

        Read by ``IndexedFreqProvider`` only when a source has no row for the
        term. The Taiwan-aware simplified spelling leads (看著 -> 看着), then
        the Taiwan traditional one (接着 -> 接著), then the lookup ladder's
        s2t/t2s variants. An ambiguous word takes the shared spelling's rank
        (麵 -> 面), which is closer than no rank at all.
        """
        candidates = [to_simplified(term), to_traditional(term), *variant_candidates(term)[1:]]
        return [c for c in dict.fromkeys(candidates) if c and c != term]


class ZhMinedFormPolicy:
    """The segmented surface in the configured Character Set.

    jieba emits no inflection, so the front is the surface, projected onto
    ``config.script_variant`` (Settings -> Filtering -> Character Set). The
    profile holds the unbound instance, which keeps the surface as written;
    runs bind one through ``registry.bound_mined_form``.
    """

    def __init__(self, script_variant: str = "") -> None:
        self._script_variant = script_variant

    def for_config(self, config: Any) -> ZhMinedFormPolicy:
        return ZhMinedFormPolicy(getattr(config, "script_variant", ""))

    def mined_form(
        self,
        pos: str | None,
        orth_base: str,
        lemma: str,
        surface: str,
        pronunciation: str | None = None,
    ) -> str:
        return to_script(surface or lemma or orth_base, self._script_variant)


class ZhLookupStrategy:
    """Simplified/traditional variants of the query, conditions=0 (pure spelling).

    The ``int`` is the Yomitan deinflection ``conditions`` bitmask. Chinese has
    no inflection, so every candidate is a pure spelling variant and emits
    ``0`` — the value ``DefinitionService._fallback_candidates`` already uses
    for orth_base and the kana folds. ``orth_base`` and ``ctype`` are part of
    the one cross-language signature and are unused here.

    The Taiwan traditional spelling leads the generic s2t one: it is what
    ``to_script`` writes on the card, so a traditional-only dictionary indexed
    from the same standard (為什麼, not s2t's 爲什麼) is reachable. Dedup is
    explicit because the two agree for most words.
    """

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        ladder = dict.fromkeys([to_traditional(word), *variant_candidates(word)])
        return [(candidate, 0) for candidate in ladder if candidate and candidate != word]
