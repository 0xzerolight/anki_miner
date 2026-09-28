"""Word forms kiwi's shipped model gets wrong, and the analysis the ko tagger uses instead.

KO_PRE_ANALYZED_FORMS  the -세요 form of three honorific verbs whose stem already
                       ends in 시. Bare kiwi reads 계세요 as 계/NNG + 이/VCP + 세요
                       (계 "savings club"), 주무세요 as 주무/NNG (主務 "being in
                       charge") and 드세요 as 드세/VA (드세다 "tough"), in every
                       frame measured, while every other form of the same verbs
                       (계셨어요, 주무시고, 드셨어요) comes out right. Each row is
                       kiwi's OWN analysis of those other forms - stem/VV +
                       어요/EF, with the overlapping character spans kiwi gives
                       계셨어요 - registered through Kiwi.add_pre_analyzed_word in
                       tokenizer._create_kiwi. Rows are whole eojeol forms, so the
                       cut never moves for any other word.

KO_PRE_ANALYZED_SCORE  the score each row is registered with. 5 wins in every
                       frame measured; at 0 kiwi still picks 드세다 in some
                       (힘이 드세요, 손 드세요).

드세요 is also 들다 + -시- ("please hold": 짐을 드세요), which bare kiwi reads as
들/VX. The row reads that as 드시다 too. KRDICT has no 드시다 row, so 드세요 makes
no card at all - accepted: a missing card beats the 드세다 "tough" card on every
식후에 드세요.
"""

from __future__ import annotations

from collections.abc import Mapping

#: One morpheme of a pre-analysed form: (form, Sejong tag, start, end), the
#: character span inside the registered eojeol.
PreAnalyzedMorpheme = tuple[str, str, int, int]

# eojeol -> the morphemes kiwi should return for it.
KO_PRE_ANALYZED_FORMS: Mapping[str, tuple[PreAnalyzedMorpheme, ...]] = {
    "계세요": (("계시", "VV", 0, 2), ("어요", "EF", 1, 3)),
    "주무세요": (("주무시", "VV", 0, 3), ("어요", "EF", 2, 4)),
    "드세요": (("드시", "VV", 0, 2), ("어요", "EF", 1, 3)),
}

KO_PRE_ANALYZED_SCORE = 5.0
