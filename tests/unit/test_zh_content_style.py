"""zh content typography data: the face list and the deliberately-identity wrap.

``zh_cjk_wrap`` returning its argument unchanged is a decision, not a stub. A
Han run is UAX #14 class ID, so a break between any two characters is correct
typography and Qt already does it; a phrase model would buy nothing and would
drag a ``languages`` -> ``gui`` import edge back in. These assertions exist so
that identity cannot be mistaken for an unfinished implementation later.
"""

from __future__ import annotations

from anki_miner.languages.profile import ContentTextStyle
from anki_miner.languages.registry import get_profile
from anki_miner.languages.zh.style import ZH_CONTENT_STYLE, ZH_FONT_FAMILIES, zh_cjk_wrap

#: Faces whose default glyph forms are Simplified.
_SIMPLIFIED_LEADING = frozenset(
    {
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "PingFang SC",
        "Hiragino Sans GB",
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
    }
)


class TestTheWrapIsIdentity:
    def test_a_sentence_comes_back_unchanged(self):
        assert zh_cjk_wrap("学习中文需要每天练习。") == "学习中文需要每天练习。"

    def test_traditional_and_mixed_text_come_back_unchanged(self):
        for text in ("學習中文需要每天練習。", "", "abc 123", "他说 OK。"):
            assert zh_cjk_wrap(text) == text

    def test_nothing_is_inserted_between_characters(self):
        text = "中文"
        assert len(zh_cjk_wrap(text)) == len(text)


class TestTheFaceList:
    def test_the_style_carries_the_module_family_list(self):
        assert ZH_CONTENT_STYLE.families == ZH_FONT_FAMILIES

    def test_the_first_face_is_simplified_leading(self):
        assert ZH_FONT_FAMILIES[0] in _SIMPLIFIED_LEADING

    def test_the_traditional_faces_are_the_tail(self):
        """An SC face renders traditional acceptably; the reverse mis-shapes."""
        last_simplified = max(i for i, name in enumerate(ZH_FONT_FAMILIES) if name in _SIMPLIFIED_LEADING)
        traditional = [i for i, name in enumerate(ZH_FONT_FAMILIES) if name not in _SIMPLIFIED_LEADING]
        assert traditional, ZH_FONT_FAMILIES
        assert min(traditional) > last_simplified

    def test_the_list_has_no_duplicates(self):
        assert len(set(ZH_FONT_FAMILIES)) == len(ZH_FONT_FAMILIES)


class TestTheProfileUsesIt:
    def test_the_zh_profile_content_style_is_this_one(self):
        style = get_profile("zh").content_style
        assert isinstance(style, ContentTextStyle)
        assert style is ZH_CONTENT_STYLE
        assert style.font_role == "zh"
        assert style.wrap is zh_cjk_wrap

    def test_the_role_is_not_the_japanese_one(self):
        """``font_role == "japanese"`` is what routes into fonts.py's helpers."""
        assert ZH_CONTENT_STYLE.font_role != get_profile("ja").content_style.font_role
