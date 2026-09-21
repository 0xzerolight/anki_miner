"""Rows jieba's shipped dictionary gets wrong, and what the zh tagger does instead.

Both tables were derived from a census of jieba's whole ``dict.txt`` crossed
with CC-CEDICT, plus what real subtitle material loses to the POS gate; every
row was checked against CC-CEDICT, and every one was kept only because its own
frame was measured against the real cut.

ZH_FLAG_OVERRIDES  jieba's flag for a word jieba's own dictionary mis-files.
                   Applied to the flag jieba emits, on the SIMPLIFIED segment,
                   after the cut - so it can never move a token boundary.

ZH_SPLIT_ENTRIES   jieba dictionary rows that fuse a verb and its object, or a
                   classifier and its noun, into one token. Removed from the
                   tagger's PRIVATE dictionary with del_word, which DOES move
                   the cut.
"""

from __future__ import annotations

from collections.abc import Mapping

# word -> the jieba flag it should carry.
ZH_FLAG_OVERRIDES: Mapping[str, str] = {
    # m (numeral) on words that are adverbs
    "一起": "d",
    "一下": "d",
    "一下子": "d",
    "十分": "d",
    "大声": "d",
    # m on pre-nominal quantity determiners (数量词). b (区别词) is the least
    # wrong class ZH_ALLOWED_POS offers - m and q are not mined at all - and it
    # is where jieba already files 主要/所有/整个. A user who unticks b in the
    # POS editor loses these five with the 区别词 they meant to drop.
    # 很多 is deliberately absent: CC-CEDICT has no row for it, so a retag could
    # never produce a card.
    "一些": "b",
    "一点": "b",
    "一点儿": "b",
    "好多": "b",
    "许多": "b",
    # m on words with a class of their own: 无数 predicates (星星无数), 大部分
    # heads a phrase (大部分是学生).
    "无数": "a",
    "大部分": "n",
    # m on pronouns and nouns
    "多少": "r",
    "左右": "n",
    "一半": "n",
    "少年": "n",
    "左手": "n",
    "一生": "n",
    # m on time words
    "一会儿": "t",
    "一会": "t",
    "半天": "t",
    # vg (bound verb morpheme) on a free verb
    "喝": "v",
    # 过 is deliberately absent. ug is jieba's only row for it, so a retag also
    # mines every aspect-marker use, which is what 过 almost always is in
    # running text (6 of 6 occurrences across the review corpora) - and the card
    # then carries the isolated reading guo4 for a neutral-tone particle.
    # j (简称略语) on an adverb (一共), on a plain noun jieba mis-files (通信),
    # and on five clippings that have lexicalised into ordinary vocabulary
    # (环保 = 环境保护, 中医, 中小学, 研发, 房地产) - j is linguistically right
    # for those five, and admitting them is a product call, not a bug fix: a
    # learner meets them as words. Clippings that name one organisation or event
    # (欧盟, 奥运会, 人大, 政协) stay out. The class itself stays out too: 43 of
    # its entries are single-character clippings (法 汉 英 港 俄) that CC-CEDICT
    # all attest, so admitting it cards them.
    "一共": "d",
    "房地产": "n",
    "通信": "n",
    "环保": "n",
    "中医": "n",
    "中小学": "n",
    "研发": "n",
}

# Fused rows: the row itself is CC-CEDICT-unattested, so it can never become a
# card and costs the learner every word it swallowed. Each one wins the route in
# a natural frame (an entry that never fires is not listed).
#
# Verb + object. Head and tail are both CC-CEDICT-attested.
ZH_SPLIT_ENTRIES: tuple[str, ...] = (
    "看电视",
    "打篮球",
    "打网球",
    "打乒乓球",
    "打麻将",
    "打毛衣",
    "打领带",
    "打广告",
    "打基础",
    "吃晚饭",
    "吃火锅",
    "吃饺子",
    "吃水果",
    "吃海鲜",
    "吃零食",
    "吃快餐",
    "喝咖啡",
    "喝啤酒",
    "喝牛奶",
    "洗衣服",
    "洗头发",
    "洗照片",
    "穿衣服",
    "换衣服",
    "换零钱",
    "做作业",
    "做手术",
    "写文章",
    "写日记",
    "玩电脑",
    "买房子",
    "买手机",
    "卖房子",
    "卖东西",
    "送礼物",
    "放寒假",
    "说笑话",
    # Classifier + noun. The row that wins the route is 本书 / 封信, not 三本 /
    # 一封信: jieba's cut of 他买了三本书 is 三/m 本书/r, so 书 is unreachable.
    # 这本 belongs to the set - without it 这本书 re-cuts to 这本|书 and the card
    # 这 is lost.
    "本书",
    "这本",
    "两本书",
    "那本书",
    "几本书",
    "整本书",
    "封信",
    "一封信",
    "两封信",
    "几封信",
    "写封信",
    "封信里",
    "十封信",
    "封信中",
)
