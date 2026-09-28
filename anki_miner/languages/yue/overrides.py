"""Words pycantonese tags wrong in every context, and the tag the yue tagger gives them.

zh's ``ZH_FLAG_OVERRIDES`` shape, reimplemented rather than imported (R32).
``YueTagger`` applies it to the tag ``pos_tag`` emits for a whole segmented word,
keyed on the engine's word, so it can never move a token boundary. Every row is
a CC-Canto or CC-CEDICT-Canto headword.
"""

from __future__ import annotations

from collections.abc import Mapping

# word -> the universal tag it should carry.
YUE_TAG_OVERRIDES: Mapping[str, str] = {
    # Set phrases. HKCanCor files its fixed expressions under X, outside
    # YUE_ALLOWED_POS, and the model follows it in context (多謝 also comes out
    # NUM), so a learner's first-week phrases never became cards. Each goes to
    # the allowed class nearest its predicate use: 唔該 is '(verb) please;
    # thanks' in CC-Canto.
    "唔該": "VERB",
    "多謝": "VERB",
    "對唔住": "VERB",
    "拜拜": "VERB",
    "唔好意思": "ADJ",
    "唔緊要": "ADJ",
    # Sentence-final particles. Standalone they come out NOUN, ADV or X (啦
    # NOUN, 吧 NOUN), and both catalogue rows list them, so they were carded as
    # '(onom.) sound of singing'.
    "啦": "PART",
    "吧": "PART",
    "喇": "PART",
    "呀": "PART",
    "啊": "PART",
    "㗎": "PART",
    "喎": "PART",
    "囉": "PART",
    "嘛": "PART",
    "咩": "PART",
    "吖": "PART",
    "啫": "PART",
    "咋": "PART",
    # Aspect markers. Standalone 緊 comes out PROPN, ADJ, NOUN or CCONJ by
    # context (做緊功課, 落緊雨). Its 'tight' sense (條褲好緊) goes with it: the
    # aspect use is the common one, and the parser's split pass frees it from
    # every glued verb (瞓緊覺 -> 瞓 緊 覺).
    "緊": "PART",
    "咗": "PART",
}
